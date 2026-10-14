"""The glossary of unit and building types: an entry for every type of the item table, read strictly."""

from __future__ import annotations

from pathlib import Path

import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.games.redalert.glossary import GLOSSARY_FILE, KINDS, SIDES, Entry, Glossary
from ucla_agent.games.redalert.items import FACTION_FAMILIES, SOVIET, Item, ItemTable

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("name", "side", "kind", "role", "strong_vs", "weak_vs")
# The kind a unit of each production queue is.
QUEUE_KINDS = {"Infantry": "infantry", "Vehicle": "vehicle", "Ship": "ship", "Aircraft": "aircraft"}
GOOD_ENTRY = (
    'name = "Heavy Tank"\nside = "Soviets"\nkind = "vehicle"\nrole = "the Soviet main battle tank"\n'
    'strong_vs = "vehicles and buildings"\nweak_vs = "rocket soldiers"\n'
)


@pytest.fixture(scope="module")
def glossary() -> Glossary:
    return Glossary.from_file(REPO_ROOT)


@pytest.fixture(scope="module")
def items() -> ItemTable:
    return ItemTable.from_file(REPO_ROOT)


def test_every_item_type_has_an_entry_with_all_six_fields_and_no_entry_names_another_type(glossary, items):
    item_types = {item.name for item in items}

    assert set(glossary) == item_types and len(glossary) == 60
    for code in glossary:
        entry = glossary.get(code)
        assert all(getattr(entry, field).strip() for field in FIELDS), code
        assert entry.side in SIDES and entry.kind in KINDS, code


def expected_side(item: Item) -> str:
    """The side the item table's factions give: both without factions, else the one family they all belong to."""
    families = {FACTION_FAMILIES[faction] for faction in item.factions}
    if not families:
        return "both"
    (family,) = families
    return "Soviets" if family == SOVIET else "Allies"


def expected_kind(item: Item) -> str:
    """The kind the item table gives: a building that shoots is a defense, a unit is its queue's, an unbuilt plane an
    aircraft."""
    if item.is_building:
        return "defense" if "defense" in item.roles else "building"
    return QUEUE_KINDS[item.queue] if item.queue is not None else "aircraft"


def test_each_entry_s_side_and_kind_agree_with_the_item_table(glossary, items):
    for item in items:
        entry = glossary.get(item.name)
        assert (entry.side, entry.kind) == (expected_side(item), expected_kind(item)), item.name


def test_each_role_is_one_short_sentence(glossary):
    for code in glossary:
        role = glossary.get(code).role
        assert ". " not in role and not role.endswith(".") and len(role) <= 100, code


def test_a_line_names_the_type_its_side_kind_and_cost_and_what_it_beats_and_what_beats_it(glossary, items):
    assert glossary.line("3tnk", items) == (
        "Heavy Tank (3tnk), Soviet vehicle, 1,150: the Soviet main battle tank: slow, tough and hard-hitting; "
        "strong vs vehicles and buildings; weak vs rocket soldiers, grenadiers and turrets"
    )
    assert glossary.line("e1", items).startswith("Rifle Infantry (e1), infantry of both sides, 100: cheap rifleman")
    assert glossary.line("pbox", items).startswith("Pillbox (pbox), Allied defense, 600: ")


def test_a_type_the_glossary_does_not_explain_has_no_line(glossary, items):
    assert (glossary.get("shok"), glossary.line("shok", items)) == (None, None)


def test_a_line_without_a_known_cost_leaves_the_cost_out():
    entry = Entry("Heavy Tank", "Soviets", "vehicle", "main battle tank", "vehicles", "rockets")

    assert (
        entry.line("3tnk", None)
        == "Heavy Tank (3tnk), Soviet vehicle: main battle tank; strong vs vehicles; weak vs rockets"
    )


def root_with(tmp_path: Path, text: str) -> Path:
    """A folder standing in for the repository root whose glossary file holds `text`."""
    path = tmp_path / GLOSSARY_FILE
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_well_formed_file_is_read_entry_by_entry(tmp_path):
    glossary = Glossary.from_file(root_with(tmp_path, "[3tnk]\n" + GOOD_ENTRY))

    assert glossary.get("3tnk") == Entry(
        "Heavy Tank", "Soviets", "vehicle", "the Soviet main battle tank", "vehicles and buildings", "rocket soldiers"
    )


@pytest.mark.parametrize(
    "text, named",
    [
        (GOOD_ENTRY.replace('weak_vs = "rocket soldiers"\n', ""), "weak_vs in [3tnk]"),
        (GOOD_ENTRY + 'cost = "1150"\n', "[3tnk] has no setting named cost"),
        (GOOD_ENTRY.replace('"Soviets"', '"Russia"'), "side in [3tnk]"),
        (GOOD_ENTRY.replace('"vehicle"', '"tank"'), "kind in [3tnk]"),
        (GOOD_ENTRY.replace('"the Soviet main battle tank"', '"  "'), "role in [3tnk]"),
        (GOOD_ENTRY.replace('"the Soviet main battle tank"', '"' + "x" * 101 + '"'), "role in [3tnk]"),
        (GOOD_ENTRY.replace('"the Soviet main battle tank"', '"two\\nlines"'), "role in [3tnk]"),
        (GOOD_ENTRY.replace('"vehicles and buildings"', "3"), "strong_vs in [3tnk]"),
    ],
    ids=[
        "a missing field",
        "an unknown key",
        "a side not allowed",
        "a kind not allowed",
        "an empty role",
        "a role too long",
        "a role of two lines",
        "a matchup that is not text",
    ],
)
def test_an_entry_that_cannot_be_used_is_a_config_error_naming_the_file_the_entry_and_the_key(tmp_path, text, named):
    root = root_with(tmp_path, "[3tnk]\n" + text)

    with pytest.raises(ConfigError) as raised:
        Glossary.from_file(root)

    assert named in str(raised.value) and str(root / GLOSSARY_FILE) in str(raised.value)


def test_an_entry_that_is_not_a_table_and_a_missing_file_are_config_errors(tmp_path):
    with pytest.raises(ConfigError, match=r"must have a \[3tnk\] table"):
        Glossary.from_file(root_with(tmp_path, '3tnk = "Heavy Tank"\n'))
    with pytest.raises(ConfigError, match="glossary.toml was not found"):
        Glossary.from_file(tmp_path / "elsewhere")
