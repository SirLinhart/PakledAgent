"""docs/MODELS.md keeps saying what config/models.toml says: every role, model file, source and setting."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path, PureWindowsPath

import pytest

from ucla_agent.core.modelserver import command, settings

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PAGE = REPO_ROOT / "docs" / "MODELS.md"
ROLE_NAMES = ["fast", "strong", "small"]
# A settings table row: | `<setting>` | `<value as the configuration file writes it>` | ...
SETTING_ROW = re.compile(r"^\| `(?P<setting>[a-z_]+)` \| `(?P<value>[^`]*)` \|", re.MULTILINE)
# A facts table row: | <name> | <text> |
FACT_ROW = re.compile(r"^\| (?P<name>[A-Z][A-Za-z0-9-]*(?: [a-z]+)?) \| (?P<text>.+) \|$", re.MULTILINE)
SOURCE = re.compile(r"^<https://huggingface\.co/[\w.-]+/[\w.-]+>, revision `[0-9a-f]{40}`$")
SHA_256 = re.compile(r"^`[0-9a-f]{64}`$")
SIZE = re.compile(r"^(?P<bytes>[\d,]+) bytes \(\d+\.\d\d GiB\)$")


@pytest.fixture(scope="module")
def page() -> str:
    return MODELS_PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def shipped() -> dict:
    """The shipped configuration as its tables, values as the file gives them."""
    with (REPO_ROOT / "config" / "models.toml").open("rb") as handle:
        return tomllib.load(handle)


def _section(page: str, heading: str) -> str:
    """The text under one heading, up to the next heading of the same or a higher level."""
    level = heading.split(" ")[0]
    start = page.index(f"\n{heading}\n") + len(heading) + 2
    following = re.search(rf"^#{{1,{len(level)}}} ", page[start:], re.MULTILINE)
    return page[start : start + following.start()] if following else page[start:]


def _documented_settings(section: str) -> dict[str, str]:
    return {row["setting"]: row["value"] for row in SETTING_ROW.finditer(section)}


def _as_written(table: dict) -> dict[str, str]:
    """Each value of a configuration table in the notation the file uses, which for these values is JSON's."""
    return {setting: json.dumps(value) for setting, value in table.items()}


def _facts(section: str) -> dict[str, str]:
    return {row["name"]: row["text"] for row in FACT_ROW.finditer(section)}


def test_the_page_has_one_section_per_role_of_the_configuration(page, shipped):
    assert list(shipped["roles"]) == ROLE_NAMES
    assert re.findall(r"^### `(\w+)`$", page, re.MULTILINE) == ROLE_NAMES


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_every_setting_of_a_role_is_listed_with_the_value_the_configuration_gives_it(page, shipped, role):
    assert _documented_settings(_section(page, f"### `{role}`")) == _as_written(shipped["roles"][role])


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_each_role_names_its_exact_model_file_its_source_its_size_and_its_checksum(page, shipped, role):
    facts = _facts(_section(page, f"### `{role}`"))
    model_file = PureWindowsPath(shipped["roles"][role]["model_path"]).name

    assert facts["Model file"] == f"`{model_file}`"
    assert SOURCE.match(facts["Source"])
    assert facts["Download"].startswith("<https://huggingface.co/") and facts["Download"].endswith(f"/{model_file}>")
    assert SIZE.match(facts["Size"])
    assert SHA_256.match(facts["SHA-256"])


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_the_download_link_is_the_source_at_the_revision_the_page_names(page, role):
    facts = _facts(_section(page, f"### `{role}`"))
    repository, revision = re.match(r"^<(?P<url>[^>]+)>, revision `(?P<revision>\w+)`$", facts["Source"]).groups()

    assert facts["Download"].startswith(f"<{repository}/resolve/{revision}/")


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_the_documented_size_is_the_size_of_the_model_file_where_it_is_installed(page, shipped, role):
    model_path = Path(shipped["roles"][role]["model_path"])
    if not model_path.is_file():
        pytest.skip(f"{model_path} is not on this machine")
    documented = SIZE.match(_facts(_section(page, f"### `{role}`"))["Size"])["bytes"]

    assert int(documented.replace(",", "")) == model_path.stat().st_size


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_the_command_line_shown_for_a_role_is_the_one_the_model_server_builds(page, role):
    loaded = settings.load(REPO_ROOT)
    index = ROLE_NAMES.index(role)
    arguments = command.build(loaded.llama_server_path, loaded.roles[index], loaded.backend_port(index))
    # The page writes paths with forward slashes on every platform.
    expected = " ".join(arguments).replace("\\", "/")

    assert f"\n```\n{expected}\n```\n" in _section(page, f"### `{role}`")


def test_every_server_and_courtesy_setting_is_listed_with_the_value_the_configuration_gives_it(page, shipped):
    documented = _documented_settings(_section(page, "## Server settings"))

    assert documented == _as_written(shipped["server"]) | _as_written(shipped["courtesy"])


def test_every_role_setting_the_loader_knows_is_explained(page):
    explained = _section(page, "## What each role setting does")
    role_settings = [field for field in settings.RoleSettings.__dataclass_fields__ if field != "name"]

    assert [setting for setting in role_settings if f"`{setting}`" not in explained] == []


def test_the_page_says_why_every_role_loads_with_no_mmap(page, shipped):
    explained = " ".join(_section(page, "## What each role setting does").split())

    assert [role for role, table in shipped["roles"].items() if table["extra_args"] != ["--no-mmap"]] == []
    assert 'Every shipped role has `extra_args = ["--no-mmap"]`' in explained
    assert "A mapped file stays resident in system RAM next to its copy in VRAM" in explained
    assert "with `--no-mmap` the process held 1.8 GB of RAM" in explained


def test_the_page_says_the_server_downloads_nothing_and_how_it_is_turned_on(page):
    plain = " ".join(page.split())

    assert "The model server never downloads anything." in plain
    assert "`config/agent.toml` has `autostart = true` in its `[models]` table" in plain
    assert "with only the roles its `autostart_roles` lists. As shipped that is `strong` and `jev9b`" in plain
    assert "about 53 GB of VRAM together, which fits the development workstation's 96 GB card" in plain
    assert "`small` is defined here but not used as shipped" in plain
    assert "If it does not start, or stops answering, the script plays Red Alert" in plain
    assert '`{"error": "unknown_role"}`' in plain and '`{"error": "role_down"}`' in plain
    assert "`runs/model-server/<role>.log`" in plain


def test_the_lease_figure_on_the_page_is_the_sum_of_the_documented_sizes_plus_the_margin(page):
    documented_bytes = [
        int(SIZE.match(_facts(_section(page, f"### `{role}`"))["Size"])["bytes"].replace(",", ""))
        for role in ROLE_NAMES
    ]
    lease_mib = -(-sum(documented_bytes) // (1024 * 1024)) + 4096

    assert f"{lease_mib:,} MiB for the three shipped roles" in page


# -- docs/COMPONENTS.md: the model server and the decision sources ---------------------------------


@pytest.fixture(scope="module")
def components_page() -> str:
    """The components page with runs of whitespace collapsed, so that wrapping does not matter."""
    return " ".join((REPO_ROOT / "docs" / "COMPONENTS.md").read_text(encoding="utf-8").split())


@pytest.fixture(scope="module")
def shipped_agent_settings() -> dict:
    with (REPO_ROOT / "config" / "agent.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_components_page_discloses_the_included_model_server(components_page, shipped, shipped_agent_settings):
    endpoint = f"http://{shipped['server']['host']}:{shipped['server']['port']}"

    assert "## Included model server" in components_page
    assert "`src/ucla_agent/core/modelserver/`" in components_page
    assert "`config/models.toml`" in components_page and "`docs/MODELS.md`" in components_page
    assert f"(`{endpoint}`)" in components_page
    assert "with only the roles `autostart_roles` lists; as shipped it is `true` with `strong` and `jev9b`" in (
        components_page
    )
    assert '`autostart = true` and `autostart_roles = ["strong", "jev9b"]` in `[models]`' in components_page
    assert "The 4B model (`small`) is defined in `config/models.toml` but not used as shipped." in components_page
    assert shipped_agent_settings["models"]["autostart"] is True
    assert shipped_agent_settings["models"]["autostart_roles"] == ["strong", "jev9b"]


def test_the_components_page_discloses_the_decision_sources(components_page, shipped_agent_settings):
    deadline = shipped_agent_settings["decide"]["default_deadline_ms"]

    assert "## Decision sources" in components_page
    assert "`src/ucla_agent/core/decide/`" in components_page
    assert "The `[decide]`, `[decide.chains]`, `[jev]` and `[jev.fields]` tables of `config/agent.toml`" in (
        components_page
    )
    assert f"(`default_deadline_ms`, {deadline:,} ms)" in components_page
    assert "the game's script makes the choice itself" in components_page
    assert "as a `decision` event" in components_page and "`fallback` event" in components_page


def test_the_components_page_says_jev_is_optional_and_off_by_default(components_page, shipped_agent_settings):
    assert "Jev is optional and off by default" in components_page
    assert "while `enabled` in the `[jev]` table is `false` no request is ever sent to it" in components_page
    assert "the Jev key is `JEV_API_KEY` in `.env`" in components_page
    assert shipped_agent_settings["jev"]["enabled"] is False
