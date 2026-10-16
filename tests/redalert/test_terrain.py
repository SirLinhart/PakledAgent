"""The terrain read from the game reference's map picture: ore patches and unbuildable cells."""

from __future__ import annotations

import builtins
import copy

import pytest

import situations
from fake_game import MAP_ID, FakeRedAlert
from situations import ask, fixture_state, new_agent
from ucla_agent.games.redalert.terrain import OrePatch, Terrain, TerrainUnknownCharacters
from ucla_agent.games.redalert.tuning import TerrainTuning, Tuning

# A 12 by 6 picture whose top-left character is the cell (10, 20).
PICTURE = [
    "............",
    ".$$.....~~..",
    ".$M...*.~~..",
    "......*..^..",
    "..T....$....",
    "..........$$",
]
ORIGIN = [10, 20]


def block(left: int, top: int, width: int, height: int) -> frozenset[tuple[int, int]]:
    """The cells of a rectangle from its top-left cell."""
    return frozenset((x, y) for y in range(top, top + height) for x in range(left, left + width))


def reference(rows=PICTURE, origin=ORIGIN, map_id: str = "arena") -> dict:
    """A game reference object picturing one map."""
    return {"rules": {"maps": {map_id: {"layout": {"origin": origin, "rows": rows, "legend": {}}}}}}


@pytest.fixture(scope="module")
def fake_reference() -> dict:
    return FakeRedAlert().game_config


def test_ore_gems_and_mines_that_touch_form_one_patch_with_its_center():
    terrain = Terrain.from_game_config(reference(), "arena")

    assert terrain.ore_patches == (
        OrePatch(center=(12, 22), cells=block(11, 21, 2, 2)),
        OrePatch(center=(16, 23), cells=frozenset({(16, 22), (16, 23), (17, 24)})),
        OrePatch(center=(21, 25), cells=block(20, 25, 2, 1)),
    )
    assert [patch.size for patch in terrain.ore_patches] == [4, 3, 2]


def test_mines_counted_per_field():
    # A field of ore with two mines apart (the second of two touching cells), and a field of gems and ore with none.
    rows = ["M$$$MM.....", "$$$$$$..$*$", "...........", "M.........."]

    terrain = Terrain.from_game_config(reference(rows, [0, 0]), "arena")

    assert [(patch.size, patch.mines) for patch in terrain.ore_patches] == [(12, 2), (3, 0), (1, 1)]
    # The mines say nothing of which field a patch is.
    assert terrain.ore_patches[1] == OrePatch(center=(9, 1), cells=frozenset({(8, 1), (9, 1), (10, 1)}))


def test_mines_a_patch_made_by_hand_has_none():
    assert OrePatch(center=(1, 1), cells=frozenset({(1, 1)})).mines == 0


def test_cells_that_touch_only_at_a_corner_belong_to_one_patch():
    terrain = Terrain.from_game_config(reference(["$..", ".$.", "..*"], [0, 0]), "arena")

    assert terrain.ore_patches == (OrePatch(center=(1, 1), cells=frozenset({(0, 0), (1, 1), (2, 2)})),)


def test_ore_a_cell_apart_is_two_patches_in_the_order_of_the_picture():
    terrain = Terrain.from_game_config(reference(["$.$", "...", "$.."], [5, 5]), "arena")

    assert [patch.center for patch in terrain.ore_patches] == [(5, 5), (7, 5), (5, 7)]


def test_a_large_field_is_one_patch():
    rows = ["." * 40] + ["." + "$" * 30 + "." * 9 for _ in range(20)]

    terrain = Terrain.from_game_config(reference(rows, [0, 0]), "arena")

    assert terrain.ore_patches == (OrePatch(center=(16, 11), cells=block(1, 1, 30, 20)),)
    assert terrain.ore_patches[0].size == 600


def test_water_rock_trees_and_ore_are_the_cells_nothing_can_be_built_on():
    terrain = Terrain.from_game_config(reference(), "arena")

    water = {(18, 21), (19, 21), (18, 22), (19, 22)}
    ore = {(11, 21), (12, 21), (11, 22), (12, 22), (16, 22), (16, 23), (17, 24), (20, 25), (21, 25)}
    assert terrain.blocked == water | ore | {(19, 23), (12, 24)}
    assert (10, 20) not in terrain.blocked


def test_the_fake_game_s_map_has_an_ore_patch_north_of_each_spawn(fake_reference):
    terrain = Terrain.from_game_config(fake_reference, MAP_ID)

    assert terrain.ore_patches == (
        OrePatch(center=(22, 40), cells=block(21, 39, 3, 3)),
        OrePatch(center=(74, 40), cells=block(73, 39, 3, 3)),
    )
    assert len(terrain.blocked) == 18 and (22, 48) not in terrain.blocked


# Two fields apart: an L of five cells in the north-west and a block of six in the south-east, ten cells by twelve.
TWO_FIELDS = [
    "..........",
    ".$$$......",
    ".$........",
    ".$........",
    "..........",
    "..........",
    "..........",
    "......*$$.",
    "......$$M.",
    "..........",
    "..........",
    "..........",
]
L_FIELD = frozenset({(1, 1), (2, 1), (3, 1), (1, 2), (1, 3)})
BLOCK_FIELD = block(6, 7, 3, 2)


def test_field_two_separate_fields_give_two_patches_each_with_its_own_ore_cells_and_their_count():
    terrain = Terrain.from_game_config(reference(TWO_FIELDS, [0, 0]), "arena")

    north_west, south_east = terrain.ore_patches

    assert (north_west.cells, south_east.cells) == (L_FIELD, BLOCK_FIELD)
    assert (north_west.size, south_east.size) == (5, 6)
    assert north_west.cells | south_east.cells <= terrain.blocked


@pytest.mark.parametrize(
    "point, nearest_north_west, nearest_south_east",
    [((9, 11), (1, 3), (8, 8)), ((0, 0), (1, 1), (6, 7)), ((5, 2), (3, 1), (6, 7)), ((2, 9), (1, 3), (6, 8))],
    ids=["from the south-east corner", "from the north-west corner", "from between them", "from the south-west"],
)
def test_field_the_ore_cell_nearest_a_point_is_the_field_s_cell_closest_to_it(
    point, nearest_north_west, nearest_south_east
):
    north_west, south_east = Terrain.from_game_config(reference(TWO_FIELDS, [0, 0]), "arena").ore_patches

    assert north_west.nearest_cell(point) == nearest_north_west
    assert south_east.nearest_cell(point) == nearest_south_east


def test_field_of_cells_as_near_the_nearest_is_the_upper_then_the_left_one():
    patch = OrePatch(center=(5, 5), cells=frozenset({(4, 5), (6, 5), (5, 4), (5, 6)}))

    assert patch.nearest_cell((5, 5)) == (5, 4)
    assert OrePatch(center=(5, 5), cells=frozenset({(6, 5), (4, 5)})).nearest_cell((5, 5)) == (4, 5)


def test_the_nearest_patch_is_the_one_whose_center_is_closest(fake_reference):
    terrain = Terrain.from_game_config(fake_reference, MAP_ID)
    home, far = terrain.ore_patches

    assert terrain.nearest_patch((22, 48)) == home
    assert terrain.nearest_patch((60, 48)) == far
    assert terrain.nearest_patch((22, 48), among=[far]) == far
    assert terrain.nearest_patch((22, 48), among=[]) is None


@pytest.mark.parametrize(
    "game_config, map_id",
    [
        (None, "arena"),
        ({}, "arena"),
        ({"rules": {"maps": {"arena": {"layout": None}}}}, "arena"),
        ({"rules": {"maps": {"arena": {"bounds": [0, 0, 9, 9]}}}}, "arena"),
        (reference(), "another-map"),
        (reference(), None),
        (reference(rows="....$$.."), "arena"),
        (reference(origin=None), "arena"),
        (reference(origin=[3]), "arena"),
    ],
    ids=[
        "no reference",
        "an empty reference",
        "a null picture",
        "a map without a picture",
        "another map",
        "no map id",
        "rows that are no list",
        "no origin",
        "half an origin",
    ],
)
def test_a_reference_without_a_usable_picture_of_the_map_gives_an_empty_terrain(game_config, map_id):
    terrain = Terrain.from_game_config(game_config, map_id)

    assert terrain.ore_patches == () and terrain.blocked == frozenset()
    assert terrain.nearest_patch((22, 48)) is None


def test_a_new_terrain_knows_nothing():
    assert (Terrain().ore_patches, Terrain().blocked) == ((), frozenset())


# -- which characters can be built on --------------------------------------------------------------


def buildable_characters_of(rows: list[str], origin: list[int], terrain: Terrain) -> set[str]:
    """The characters of the picture's cells that the terrain does not block."""
    return {
        character
        for line, row in enumerate(rows)
        for column, character in enumerate(row)
        if (origin[0] + column, origin[1] + line) not in terrain.blocked
    }


@pytest.mark.parametrize(
    "characters",
    [".,=S~^T$*M#H:X", ".,=:H#~^T$*MS"],
    ids=["the requirement's characters", "every character of the harness legend"],
)
def test_only_clear_road_and_spawn_cells_can_be_built_on(characters):
    rows = [characters, characters[::-1]]

    terrain = Terrain.from_game_config(reference(rows, [5, 7]), "arena")

    assert buildable_characters_of(rows, [5, 7], terrain) == {".", "=", "S"}
    assert len(terrain.blocked) == 2 * (len(characters) - 3)
    assert {(6, 7), (5 + len(characters) - 2, 8)} <= terrain.blocked


def test_the_ore_patches_are_those_of_ore_gems_and_mines_whatever_else_the_picture_shows():
    rows = [".,=S~^T$*M#H:X"]

    terrain = Terrain.from_game_config(reference(rows, [5, 7]), "arena")

    assert terrain.ore_patches == (OrePatch(center=(13, 7), cells=block(12, 7, 3, 1)),)
    assert {(12, 7), (13, 7), (14, 7)} <= terrain.blocked


def test_the_buildable_characters_given_replace_the_built_in_ones():
    terrain = Terrain.from_game_config(reference([".,=S~"], [0, 0]), "arena", ".=~")

    assert terrain.blocked == {(1, 0), (3, 0)}


def test_ore_listed_as_buildable_is_still_ore_and_blocked():
    terrain = Terrain.from_game_config(reference(["$*M."], [0, 0]), "arena", ".$*M")

    assert terrain.blocked == {(0, 0), (1, 0), (2, 0)}
    assert terrain.ore_patches == (OrePatch(center=(1, 0), cells=block(0, 0, 3, 1)),)


def test_a_missing_picture_blocks_nothing_whatever_characters_are_buildable():
    terrain = Terrain.from_game_config(None, "arena", ".")

    assert terrain.blocked == frozenset() and terrain.ore_patches == ()


def test_the_agent_reads_the_map_with_the_buildable_characters_of_its_tuning():
    only_spawns = Tuning(terrain=TerrainTuning(buildable_characters="S"))
    agents = {"built-in": new_agent(), "only spawns": new_agent(tuning=only_spawns)}

    for agent in agents.values():
        ask(agent, fixture_state("opening_mcv"), situations.context(FakeRedAlert().game_config))

    built_in, spawns_only = (agent.ctx.terrain.blocked for agent in agents.values())
    assert len(built_in) == 18 and (21, 48) not in built_in
    assert (21, 48) in spawns_only and (22, 48) not in spawns_only and len(spawns_only) == 96 * 96 - 2


def test_reading_the_picture_opens_no_file_and_does_not_change_the_reference(fake_reference, monkeypatch):
    before = copy.deepcopy(fake_reference)
    opened = []
    monkeypatch.setattr(builtins, "open", lambda *arguments, **options: opened.append(arguments))

    Terrain.from_game_config(fake_reference, MAP_ID)

    assert opened == [] and fake_reference == before


# -- characters the agent does not know ------------------------------------------------------------


def test_each_unknown_character_is_counted_with_the_cells_of_the_whole_picture():
    rows = ["..##HX", "~~^T$:"]

    terrain = Terrain.from_game_config(reference(rows, [0, 0]), "arena")

    assert terrain.unknown_cells == {"#": 2, ":": 1, "H": 1, "X": 1} and terrain.cell_count == 12


def test_a_character_listed_as_buildable_or_known_to_be_blocked_is_not_unknown():
    terrain = Terrain.from_game_config(reference(["#~^T$*M,.=S"], [0, 0]), "arena", ".=S#")

    assert terrain.unknown_cells == {} and terrain.cell_count == 11


@pytest.mark.parametrize(
    "rows, error",
    [
        (["XXX..", "....."], True),
        (["XX...", "....."], False),
        (["X#...", "....."], False),
        (["X#H..", "....."], True),
        (["X#H" + "." * 12], False),
    ],
    ids=["0.3 of one character", "exactly 0.2", "0.2 of two", "0.3 of three", "three fifteenths"],
)
def test_unknown_characters_are_an_error_only_above_the_share_given(rows, error):
    terrain = Terrain.from_game_config(reference(rows, [0, 0]), "arena")

    found = terrain.unknown_characters_error(0.2)

    assert isinstance(found, TerrainUnknownCharacters) if error else found is None


def test_the_error_names_each_unknown_character_its_share_and_their_sum():
    terrain = Terrain.from_game_config(reference(["X#H..", "....."], [0, 0]), "arena")

    message = str(terrain.unknown_characters_error(0.2))

    assert "cover 0.300 of the map picture, more than 0.2" in message
    assert message.endswith("'#' 0.100, 'H' 0.100, 'X' 0.100")


def test_a_terrain_read_from_no_picture_has_no_unknown_characters():
    assert Terrain().unknown_characters_error(0.0) is None
    assert Terrain.from_game_config(None, "arena").unknown_characters_error(0.0) is None


def reference_with_rows_of(character: str, count: int) -> dict:
    """The fake game's reference whose map picture is ten rows of ten cells, the first `count` of them `character`."""
    game_config = FakeRedAlert().game_config
    rows = [character * 10] * count + ["." * 10] * (10 - count)
    game_config["rules"]["maps"][MAP_ID]["layout"].update(origin=[0, 0], rows=rows)
    return game_config


def test_a_picture_30_percent_unknown_logs_one_handled_error_and_the_match_goes_on_with_those_cells_blocked(
    match_record,
):
    game_config = reference_with_rows_of("X", 3)
    agent = new_agent()

    ask(agent, fixture_state("opening_mcv", 0), situations.context(game_config))
    ask(agent, fixture_state("opening_mcv", 5), situations.context(game_config))

    (error,) = match_record("error")
    assert {key: error[key] for key in ("where", "type", "exception")} == {
        "where": "terrain",
        "type": "handled",
        "exception": "TerrainUnknownCharacters",
    }
    assert "cover 0.300 of the map picture" in error["message"] and "'X' 0.300" in error["message"]
    assert {(x, y) for x in range(10) for y in range(3)} <= agent.ctx.terrain.blocked
    assert agent.world.tick == 5


def test_a_picture_10_percent_unknown_logs_no_error(match_record):
    agent = new_agent()

    ask(agent, fixture_state("opening_mcv", 0), situations.context(reference_with_rows_of("X", 1)))

    assert match_record("error") == []
    assert {(x, 0) for x in range(10)} <= agent.ctx.terrain.blocked


def test_a_picture_30_percent_rough_ground_logs_no_error_and_its_cells_stay_blocked(match_record):
    agent = new_agent()

    ask(agent, fixture_state("opening_mcv", 0), situations.context(reference_with_rows_of(",", 3)))

    assert match_record("error") == []
    assert agent.ctx.terrain.unknown_cells == {}
    assert {(x, y) for x in range(10) for y in range(3)} <= agent.ctx.terrain.blocked


# -- walking -------------------------------------------------------------------------------------------------------------


def test_water_rock_and_trees_are_the_cells_no_unit_walks_and_every_other_character_is_passable():
    rows = ["~^T.,#H:$="]
    terrain = Terrain.from_game_config(reference(rows, [0, 0]), "arena")

    assert terrain.impassable == {(0, 0), (1, 0), (2, 0)}
    assert Terrain().impassable == frozenset() and Terrain.from_game_config({}, "arena").impassable == frozenset()


def test_the_walk_distance_goes_round_a_wall_and_counts_a_corner_step_as_one():
    field = OrePatch(center=(5, 5), cells=frozenset({(5, 5)}))
    # A wall of 3 cells right of the ore: the cell behind it is 2 steps away in a straight line, 4 round the wall.
    walled = Terrain([field], impassable=[(6, 4), (6, 5), (6, 6)])

    assert Terrain([field]).walk_distances(field, 2)[(7, 7)] == 2
    assert walled.walked_distance([(7, 5)], field, 4) == 4
    assert walled.walked_distance([(7, 5)], field, 3) is None
    assert walled.walked_distance([(7, 5), (4, 5)], field, 4) == 1
    assert (6, 5) not in walled.walk_distances(field, 4) and walled.walk_distances(field, 4)[(5, 5)] == 0


def test_the_walk_zone_is_the_cells_within_reach_by_walking_and_never_more_than_a_king_s_moves():
    field = OrePatch(center=(5, 5), cells=frozenset({(5, 5), (5, 6)}))
    walled = Terrain([field], impassable=[(6, 4), (6, 5), (6, 6), (6, 7)])

    open_zone, walled_zone = Terrain([field]).walk_zone(field, 1), walled.walk_zone(field, 1)

    assert open_zone == {(x, y) for x in (4, 5, 6) for y in (4, 5, 6, 7)}
    assert walled_zone == open_zone - {(6, 4), (6, 5), (6, 6), (6, 7)}
