"""Group bookkeeping: the home guard's quotas are filled first, every other fighting unit joins main, and a member
aboard a transport keeps its group."""

from __future__ import annotations

from pathlib import Path

import pytest

from ucla_agent.games.redalert.fight import strength
from ucla_agent.games.redalert.groups import GUARD, MAIN, RAID, SCOUT, Groups
from ucla_agent.games.redalert.items import ItemTable
from ucla_agent.games.redalert.world import World


def world_with(*units: tuple[int, str]) -> World:
    """A world whose own units are the given (id, type) pairs, in that order."""
    world = World(ItemTable([]))
    listed = [{"id": unit_id, "type": unit_type, "x": 20, "y": 48, "hp": 100} for unit_id, unit_type in units]
    world.update({"phase": "playing", "observation": {"tick": 100, "units": listed}})
    return world


def rifles(first_id: int, count: int) -> list[tuple[int, str]]:
    return [(first_id + number, "e1") for number in range(count)]


@pytest.fixture
def groups() -> Groups:
    """Groups whose home guard wants 5 rifle infantry."""
    groups = Groups()
    groups.set_guard_quota("e1", 5)
    return groups


def test_new_groups_have_no_members():
    groups = Groups()

    assert (groups.members(GUARD), groups.members(MAIN), groups.count(GUARD, "e1")) == ([], [], 0)


def test_the_first_five_rifle_infantry_join_the_guard_and_the_sixth_joins_main(groups):
    groups.update(world_with(*rifles(200, 6)))

    assert groups.members(GUARD) == [200, 201, 202, 203, 204]
    assert groups.members(MAIN) == [205]
    assert (groups.count(GUARD, "e1"), groups.count(MAIN, "e1")) == (5, 1)


def test_units_seen_one_state_after_another_fill_the_guard_in_the_order_they_appear(groups):
    for seen in range(1, 8):
        groups.update(world_with(*rifles(200, seen)))

    assert groups.members(GUARD) == [200, 201, 202, 203, 204]
    assert groups.members(MAIN) == [205, 206]


def test_a_unit_stays_in_its_group_from_state_to_state(groups):
    groups.update(world_with(*rifles(200, 6)))
    groups.update(world_with(*reversed(rifles(200, 6))))

    assert groups.members(GUARD) == [200, 201, 202, 203, 204] and groups.members(MAIN) == [205]


def test_a_unit_that_is_gone_is_forgotten_and_the_next_of_its_type_takes_its_place_in_the_guard(groups):
    groups.update(world_with(*rifles(200, 6)))

    groups.update(world_with(*rifles(201, 5)))
    after_the_loss = (groups.members(GUARD), groups.members(MAIN), groups.count(GUARD, "e1"))
    groups.update(world_with(*rifles(201, 7)))

    assert after_the_loss == ([201, 202, 203, 204], [205], 4)
    assert groups.members(GUARD) == [201, 202, 203, 204, 206]
    assert groups.members(MAIN) == [205, 207]


def test_a_type_the_guard_has_no_quota_for_joins_main(groups):
    groups.update(world_with((300, "e3"), (301, "1tnk"), (302, "e1")))

    assert groups.members(MAIN) == [300, 301] and groups.members(GUARD) == [302]
    assert (groups.count(MAIN, "e3"), groups.count(MAIN, "1tnk"), groups.count(GUARD, "e3")) == (1, 1, 0)


def test_a_quota_set_later_takes_the_units_trained_from_then_on(groups):
    groups.update(world_with((300, "e3")))

    groups.set_guard_quota("e3", 4)
    groups.update(world_with((300, "e3"), *((310 + number, "e3") for number in range(5))))

    assert groups.members(GUARD) == [310, 311, 312, 313]
    assert groups.members(MAIN) == [300, 314]
    assert (groups.count(GUARD, "e3"), groups.count(MAIN, "e3")) == (4, 2)


def test_a_quota_is_counted_per_type(groups):
    groups.set_guard_quota("e3", 2)

    groups.update(world_with(*rifles(200, 6), (300, "e3"), (301, "e3"), (302, "e3")))

    assert groups.members(GUARD) == [200, 201, 202, 203, 204, 300, 301]
    assert groups.members(MAIN) == [205, 302]


def test_units_that_do_not_fight_are_in_no_group(groups):
    groups.update(world_with((100, "mcv"), (101, "harv"), (102, "e6"), (103, "medi"), (104, "e1")))

    assert groups.members(GUARD) == [104] and groups.members(MAIN) == []


def test_a_wreck_is_in_no_group_and_a_unit_that_became_one_leaves_its_group(groups):
    groups.update(world_with((400, "1tnk"), (401, "e1")))

    groups.update(world_with((400, "1tnk.husk"), (401, "e1")))

    assert groups.members(MAIN) == [] and groups.members(GUARD) == [401]
    assert groups.count(MAIN, "1tnk") == 0


def test_two_matches_share_no_bookkeeping():
    first, second = Groups(), Groups()
    first.set_guard_quota("e1", 5)

    first.update(world_with(*rifles(200, 3)))
    second.update(world_with(*rifles(200, 3)))

    assert first.members(GUARD) == [200, 201, 202] and second.members(GUARD) == []
    assert second.members(MAIN) == [200, 201, 202]


# -- moving units between groups -------------------------------------------------------------------


def test_a_unit_assigned_to_another_group_leaves_its_old_group_and_is_counted_in_the_new_one(groups):
    groups.update(world_with(*rifles(200, 6), (300, "1tnk")))

    groups.assign(205, SCOUT)
    groups.assign(300, RAID)

    assert groups.members(SCOUT) == [205] and groups.members(RAID) == [300] and groups.members(MAIN) == []
    assert (groups.count(MAIN, "e1"), groups.count(SCOUT, "e1")) == (0, 1)
    assert (groups.count(MAIN, "1tnk"), groups.count(RAID, "1tnk")) == (0, 1)
    assert (groups.group_of(205), groups.group_of(300), groups.group_of(200)) == (SCOUT, RAID, GUARD)


def test_an_assigned_unit_keeps_its_new_group_from_state_to_state_and_joins_last(groups):
    groups.update(world_with(*rifles(200, 6)))
    groups.assign(205, SCOUT)

    groups.assign(205, GUARD)
    groups.update(world_with(*rifles(200, 7)))

    assert groups.members(GUARD) == [200, 201, 202, 203, 204, 205]
    assert groups.members(MAIN) == [206] and groups.count(GUARD, "e1") == 6


def test_an_assigned_unit_that_is_gone_is_forgotten_and_counted_no_more(groups):
    groups.update(world_with(*rifles(200, 6)))
    groups.assign(205, SCOUT)

    groups.update(world_with(*rifles(200, 5)))

    assert groups.members(SCOUT) == [] and groups.count(SCOUT, "e1") == 0 and groups.group_of(205) is None


def test_a_unit_that_does_not_fight_has_no_group(groups):
    groups.update(world_with((100, "harv"), (101, "e1")))

    assert (groups.group_of(100), groups.group_of(101)) == (None, GUARD)


# -- the marks base defense sets -------------------------------------------------------------------


def test_new_groups_carry_no_override_and_no_recall():
    groups = Groups()

    assert [groups.has_defense_override(group) for group in (GUARD, MAIN, RAID, SCOUT)] == [False] * 4
    assert [groups.is_recalled(group) for group in (GUARD, MAIN, RAID, SCOUT)] == [False] * 4


def test_override_and_recall_are_set_per_group_and_released_together():
    groups = Groups()
    groups.set_defense_override(GUARD)
    groups.set_defense_override(MAIN)
    groups.recall(RAID)
    groups.recall(MAIN)
    marked = (groups.has_defense_override(MAIN), groups.has_defense_override(RAID), groups.is_recalled(RAID))

    released = groups.release_defense()

    assert marked == (True, False, True)
    assert released == [MAIN, RAID]
    assert not groups.has_defense_override(GUARD) and not groups.is_recalled(RAID)
    assert groups.release_defense() == []


def marked_groups() -> Groups:
    """Groups of main's three rifles 300-302 and the raid's two 303-304, before any mark."""
    groups = Groups()
    groups.update(world_with(*rifles(300, 5)))
    for unit_id in (303, 304):
        groups.assign(unit_id, RAID)
    return groups


def test_unit_override_marks_the_units_given_and_not_the_rest_of_their_group():
    groups = marked_groups()

    groups.set_unit_override([301, 303])

    assert [groups.is_overridden(unit_id) for unit_id in range(300, 305)] == [False, True, False, True, False]
    assert not groups.has_defense_override(MAIN) and not groups.has_defense_override(RAID)
    assert (groups.is_any_overridden(MAIN), groups.is_any_overridden(RAID), groups.is_any_overridden(GUARD)) == (
        True,
        True,
        False,
    )


def test_unit_override_a_group_under_the_override_has_every_member_overridden():
    groups = marked_groups()

    groups.set_defense_override(MAIN)

    assert [groups.is_overridden(unit_id) for unit_id in range(300, 305)] == [True, True, True, False, False]
    assert groups.is_any_overridden(MAIN) and not groups.is_any_overridden(RAID)


def test_unit_override_a_group_under_the_override_counts_though_it_has_no_member_left():
    groups = Groups()

    groups.set_defense_override(RAID)

    assert groups.is_any_overridden(RAID) and not groups.is_overridden(303)


def test_unit_override_the_unit_marks_are_released_with_the_group_marks():
    groups = marked_groups()
    groups.set_unit_override([300, 304])
    groups.set_defense_override(GUARD)

    released = groups.release_defense()

    assert released == [] and not any(groups.is_overridden(unit_id) for unit_id in range(300, 305))
    assert not groups.is_any_overridden(MAIN) and not groups.is_any_overridden(RAID)


# -- members aboard a transport --------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
APC = 500
# Two rocket soldiers of the raid group: one boards the APC, the other stays outside.
RIDER, WALKER = 501, 502
UNLOAD = {"cmd": "unload", "units": [APC]}


def seen(world: World, tick: int, *units: tuple[int, str]) -> World:
    """The world after one more state, of that tick, listing these (id, type) own units, all at full health."""
    listed = [{"id": unit_id, "type": unit_type, "x": 40, "y": 40, "hp": 100} for unit_id, unit_type in units]
    world.update({"phase": "playing", "observation": {"tick": tick, "units": listed}})
    return world


@pytest.fixture
def boarded() -> tuple[World, Groups]:
    """A raid group of two rocket soldiers, one of which boarded the APC in the state of tick 105."""
    world, groups = World(ItemTable.from_file(REPO_ROOT)), Groups()
    groups.set_raid_quota({"e3": 2})
    groups.update(seen(world, 100, (APC, "apc"), (RIDER, "e3"), (WALKER, "e3")))
    world.note_orders([{"cmd": "enter_transport", "units": [RIDER], "target": APC}])
    groups.update(seen(world, 105, (APC, "apc"), (WALKER, "e3")))
    return world, groups


def test_a_raid_member_that_boards_stays_on_the_raid_roster_and_counts_toward_its_quota_while_aboard(boarded):
    world, groups = boarded

    groups.update(seen(world, 110, (APC, "apc"), (WALKER, "e3"), (503, "e3")))

    assert groups.group_of(RIDER) == RAID and groups.roster(RAID) == [RIDER, WALKER]
    assert groups.aboard(RAID) == [RIDER] and groups.count(RAID, "e3") == 2
    # The raid holds its quota with the member aboard, so a new rocket soldier goes to main.
    assert groups.group_of(503) == MAIN


def test_a_raid_member_aboard_is_not_in_the_raid_group_s_strength_in_the_field(boarded):
    world, groups = boarded

    in_the_field = [world.combat_units[unit_id] for unit_id in groups.members(RAID)]

    assert groups.members(RAID) == [WALKER] and RIDER not in world.combat_units
    assert strength(in_the_field, world) == strength([world.combat_units[WALKER]], world) == 300


def test_a_member_aboard_is_no_long_range_unit_in_the_field():
    # The bookkeeping takes aboard whatever the agent ordered in; which types a transport carries is the engine's.
    world, groups = World(ItemTable.from_file(REPO_ROOT)), Groups()
    groups.update(seen(world, 100, (APC, "apc"), (600, "arty"), (601, "arty")))
    world.note_orders([{"cmd": "enter_transport", "units": [600], "target": APC}])

    groups.update(seen(world, 105, (APC, "apc"), (601, "arty")))

    assert groups.siege_members(MAIN) == [601] and groups.roster(MAIN) == [APC, 600, 601]


def test_a_member_aboard_a_transport_that_is_lost_leaves_its_group_and_its_count(boarded):
    world, groups = boarded

    groups.update(seen(world, 110, (WALKER, "e3")))

    assert groups.roster(RAID) == [WALKER] and groups.count(RAID, "e3") == 1 and groups.group_of(RIDER) is None


def test_a_member_that_comes_out_of_its_transport_is_in_the_field_again_in_its_group(boarded):
    world, groups = boarded
    world.note_orders([UNLOAD])

    groups.update(seen(world, 110, (APC, "apc"), (RIDER, "e3"), (WALKER, "e3")))

    assert groups.members(RAID) == [RIDER, WALKER] and groups.aboard(RAID) == []


def test_a_member_that_comes_out_under_a_new_id_keeps_its_group_and_its_place_under_that_id(boarded):
    world, groups = boarded
    world.note_orders([UNLOAD])

    groups.update(seen(world, 110, (APC, "apc"), (WALKER, "e3"), (601, "e3")))

    assert groups.members(RAID) == [601, WALKER] and groups.group_of(RIDER) is None
    assert groups.count(RAID, "e3") == 2


def test_a_member_unloaded_that_does_not_come_back_into_sight_within_100_ticks_leaves_its_group(boarded):
    world, groups = boarded
    world.note_orders([UNLOAD])

    groups.update(seen(world, 205, (APC, "apc"), (WALKER, "e3")))
    still_aboard = groups.aboard(RAID)
    groups.update(seen(world, 206, (APC, "apc"), (WALKER, "e3")))

    assert still_aboard == [RIDER]
    assert groups.roster(RAID) == [WALKER] and groups.group_of(RIDER) is None


def test_a_member_aboard_can_be_moved_to_another_group_and_stays_aboard_there(boarded):
    world, groups = boarded

    groups.assign(RIDER, MAIN)
    groups.update(seen(world, 110, (APC, "apc"), (WALKER, "e3")))

    assert groups.aboard(MAIN) == [RIDER] and groups.members(MAIN) == [APC]
    assert (groups.count(MAIN, "e3"), groups.count(RAID, "e3")) == (1, 1)


# -- members walking a route ----------------------------------------------------------------------


def test_walked_route_a_walking_member_keeps_its_group_and_count_but_is_out_of_members_until_unmarked(groups):
    groups.update(world_with(*rifles(200, 6)))

    groups.set_walking(201, True)
    marked = (groups.members(GUARD), groups.roster(GUARD), groups.count(GUARD, "e1"), groups.group_of(201))
    groups.set_walking(201, False)

    assert marked == ([200, 202, 203, 204], [200, 201, 202, 203, 204], 5, GUARD)
    assert groups.members(GUARD) == [200, 201, 202, 203, 204] and not groups.is_walking(201)


def test_walked_route_a_walking_member_holds_its_place_in_the_guard_s_quota_so_a_new_rifle_goes_to_main(groups):
    groups.update(world_with(*rifles(200, 5)))
    groups.set_walking(202, True)

    groups.update(world_with(*rifles(200, 6)))

    assert groups.is_walking(202) and groups.group_of(205) == MAIN


def test_walked_route_the_walking_mark_of_a_unit_that_is_gone_is_dropped(groups):
    groups.update(world_with(*rifles(200, 3)))
    groups.set_walking(201, True)

    groups.update(world_with((200, "e1"), (202, "e1")))
    groups.update(world_with(*rifles(200, 3)))

    assert not groups.is_walking(201) and groups.members(GUARD) == [200, 202, 201]
