"""The decision settings of config/agent.toml and the chains built from them."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import ChoiceRequest, ClaudeSettings, JevFields, Option, build_chain, settings
from ucla_agent.core.decide.settings import DecideSettings, JevSettings, SystemoneSettings
from ucla_agent.core.modelserver.systemone import SYSTEMONE_ROUTE

REPO_ROOT = Path(__file__).resolve().parents[2]
JEV_KEY = "jev-4f9c2d7e1a6b8053"
MODELS = """
[models]
autostart = false
"""
DECIDE = """
[decide]
default_deadline_ms = 1500
choice_reasons = true
"""
CHAINS = """
[decide.chains]
red_alert_plan = ["jev", "local:fast"]
werewolf_vote = ["local:strong"]
"""
JEV = """
[jev]
enabled = false
base_url = "https://api.jevai.net"
min_confidence = 0.55
"""
CLAUDE = """
[claude]
enabled = false
base_url = "https://api.anthropic.com"
api_version = "2023-06-01"
max_tokens_per_match = 300000

[claude.models]
sonnet = "claude-sonnet-4-5"
"""
MODELS_TOML = """
[server]
host = "127.0.0.1"
port = 11777
backend_port_base = 41500

[roles.fast]
model_path = "C:/Models/gguf/fast.gguf"
required = false
ctx_size = 8192
temperature = 0
reasoning = "off"

[roles.strong]
model_path = "C:/Models/gguf/strong.gguf"
required = false
ctx_size = 16384
temperature = 0
reasoning = "off"
"""
REQUEST = ChoiceRequest("Which plan?", [Option("HOLD", "Hold."), Option("RAID", "Raid.")], "Minute 6.", 1500, "1250")


def _write(root: Path, agent_toml: str, models_toml: str | None = MODELS_TOML) -> Path:
    """Write the configuration files; the agent file gets the `[claude]` tables after what the test gives."""
    config = root / "config"
    config.mkdir(exist_ok=True)
    (config / "agent.toml").write_bytes((agent_toml + CLAUDE).encode("utf-8"))
    if models_toml is not None:
        (config / "models.toml").write_bytes(models_toml.encode("utf-8"))
    return config / "agent.toml"


def _load_error(root: Path, agent_toml: str) -> str:
    path = _write(root, agent_toml)
    with pytest.raises(ConfigError) as raised:
        settings.load(root)
    assert str(path) in str(raised.value)
    return str(raised.value)


class _Services:
    """Stands in for the network: the Jev service and the model server, each answering as the test says."""

    def __init__(self, jev_confidence: float = 0.9, model_choice: str = "HOLD") -> None:
        self.requests: list[httpx.Request] = []
        self._jev_confidence = jev_confidence
        self._model_choice = model_choice
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.jevai.net":
            return httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": self._jev_confidence})
        if request.url.path == SYSTEMONE_ROUTE:
            answer = {"type": "choice", "choice": self._model_choice, "confidence": 0.9}
            return httpx.Response(200, json={"model": "JevK5-9B", "answers": {"action": answer}})
        content = json.dumps({"choice": self._model_choice})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


@pytest.fixture
def key(run_folder, monkeypatch) -> None:
    monkeypatch.setenv("JEV_API_KEY", JEV_KEY)


# -- the shipped file ------------------------------------------------------------------------------


def test_the_shipped_file_sets_the_deadline_the_red_alert_werewolf_and_pokemon_chains_and_jev_and_claude_off():
    assert settings.load(REPO_ROOT) == DecideSettings(
        default_deadline_ms=1500,
        choice_reasons=True,
        chains={
            "red_alert_plan": ("systemone:jev9b",),
            "red_alert_scout": ("systemone:jev9b",),
            "red_alert_commander": ("systemone:jev9b",),
            "werewolf_choice": ("local:strong",),
            "werewolf_message": ("local:strong",),
            "pokemon_choice": ("local:strong",),
        },
        jev=JevSettings(enabled=False, base_url="https://api.jevai.net", min_confidence=0.55, fields=JevFields()),
        claude=ClaudeSettings(
            enabled=False,
            base_url="https://api.anthropic.com",
            api_version="2023-06-01",
            max_tokens_per_match=300000,
            models={"sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-1", "haiku": "claude-haiku-4-5"},
        ),
        systemone={"jev9b": SystemoneSettings(base_url=None, min_confidence=0.0)},
    )


def test_every_chain_of_the_shipped_files_can_be_built_and_asks_the_servers_of_the_shipped_models_file(
    run_folder, monkeypatch
):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")
    services = _Services()

    chains = settings.load(run_folder).chains
    answers = [build_chain(name, transport=services.transport).choose(REQUEST) for name in chains]

    # The three Red Alert chains ask the jev9b systemone role on its own port (backend_port_base + 3); the
    # Werewolf and Pokemon chains ask strong through the endpoint. The transport is a stub: no socket is opened.
    assert services.urls() == ["http://127.0.0.1:41503/v1/systemone"] * 3 + [
        "http://127.0.0.1:11500/v1/chat/completions"
    ] * 3
    assert [json.loads(request.content)["model"] for request in services.requests[3:]] == ["strong"] * 3
    assert [answer.option_id for answer in answers] == ["HOLD"] * 6


# -- reading ---------------------------------------------------------------------------------------


def test_each_chain_keeps_its_entries_in_order(tmp_path):
    _write(tmp_path, MODELS + DECIDE + CHAINS + JEV)

    assert settings.load(tmp_path).chains == {
        "red_alert_plan": ("jev", "local:fast"),
        "werewolf_vote": ("local:strong",),
    }


def test_a_file_without_a_chains_table_has_no_chains(tmp_path):
    _write(tmp_path, MODELS + DECIDE + JEV)

    assert settings.load(tmp_path).chains == {}


def test_the_names_of_jev_s_fields_can_be_set_one_by_one(tmp_path):
    fields = '\n[jev.fields]\nvalue = "decision"\nconfidence = "scores.confidence"\n'
    _write(tmp_path, MODELS + DECIDE + JEV + fields)

    assert settings.load(tmp_path).jev.fields == JevFields(
        state="state", output_schema="output_schema", value="decision", confidence="scores.confidence"
    )


@pytest.mark.parametrize(
    "agent_toml, expected",
    [
        (MODELS + JEV, "must have a [decide] table"),
        (MODELS + DECIDE, "must have a [jev] table"),
        (MODELS + "[decide]\n" + JEV, "default_deadline_ms in [decide] must be a whole number of at least 1"),
        (MODELS + "[decide]\ndefault_deadline_ms = 0\n" + JEV, "default_deadline_ms in [decide] must be"),
        (MODELS + '[decide]\ndefault_deadline_ms = "1500"\n' + JEV, "default_deadline_ms in [decide] must be"),
        (MODELS + DECIDE + "retries = 2\n" + JEV, "[decide] has no setting named retries"),
        (MODELS + DECIDE + "chains = 3\n" + JEV, "must have a [decide.chains] table"),
        (MODELS + DECIDE + JEV.replace("enabled = false\n", ""), "enabled in [jev] must be true or false"),
        (MODELS + DECIDE + JEV.replace("false", '"no"'), "enabled in [jev] must be true or false"),
        (MODELS + DECIDE + JEV.replace("min_confidence = 0.55", ""), "min_confidence in [jev] must be a number"),
        (MODELS + DECIDE + JEV.replace("0.55", '"high"'), "min_confidence in [jev] must be a number"),
        (MODELS + DECIDE + JEV.replace('base_url = "https://api.jevai.net"', ""), "base_url in [jev] must be a string"),
        (MODELS + DECIDE + JEV + "timeout_ms = 500\n", "[jev] has no setting named timeout_ms"),
        (MODELS + DECIDE + JEV + '\n[jev.fields]\nanswer = "value"\n', "[jev.fields] has no setting named answer"),
        (MODELS + DECIDE + JEV + "\n[jev.fields]\nvalue = 3\n", "value in [jev.fields] must be a string"),
    ],
    ids=[
        "no decide table",
        "no jev table",
        "no deadline",
        "deadline zero",
        "deadline a string",
        "unknown decide key",
        "chains is a value",
        "no enabled",
        "enabled a string",
        "no min_confidence",
        "min_confidence a string",
        "no base_url",
        "unknown jev key",
        "unknown field name",
        "field name a number",
    ],
)
def test_a_setting_that_is_missing_of_the_wrong_kind_or_unknown_is_reported_with_its_table(
    tmp_path, agent_toml, expected
):
    assert expected in _load_error(tmp_path, agent_toml)


@pytest.mark.parametrize("base_url", ["http://api.jevai.net", "api.jevai.net", "ftp://api.jevai.net", ""])
def test_the_jev_base_url_must_use_tls(tmp_path, base_url):
    message = _load_error(tmp_path, MODELS + DECIDE + JEV.replace("https://api.jevai.net", base_url))

    assert "base_url in [jev] must be a URL that starts with https://" in message


@pytest.mark.parametrize(
    "entries",
    ['["remote:claude"]', '["local:"]', '["local:fast", "Jev"]', '[""]', '"local:fast"', "[1]"],
    ids=["another kind", "no role", "wrong case", "empty", "not a list", "a number"],
)
def test_a_chain_entry_must_be_local_role_or_jev(tmp_path, entries):
    message = _load_error(tmp_path, MODELS + DECIDE + f"\n[decide.chains]\nplan = {entries}\n" + JEV)

    assert "plan in [decide.chains] must be a list of" in message


def test_the_agent_configuration_itself_must_be_usable(tmp_path):
    _write(tmp_path, DECIDE + JEV)

    with pytest.raises(ConfigError, match="must set autostart"):
        settings.load(tmp_path)


# -- a short reason with each choice ---------------------------------------------------------------

CLAUDE_KEY = "claude-test-key-5e1b7c9a3d26f804"
REASON = "base threatened, army weaker"


@pytest.mark.parametrize("value", ['"yes"', "1", "[]"], ids=["a string", "a number", "a list"])
def test_reason_switch_that_is_not_true_or_false_is_a_configuration_error_naming_it(tmp_path, value):
    message = _load_error(tmp_path, MODELS + DECIDE.replace("choice_reasons = true", f"choice_reasons = {value}") + JEV)

    assert "choice_reasons in [decide] must be true or false" in message


def test_reason_switch_left_out_is_a_configuration_error_naming_it(tmp_path):
    message = _load_error(tmp_path, MODELS + DECIDE.replace("choice_reasons = true\n", "") + JEV)

    assert "choice_reasons in [decide] must be true or false" in message


def test_reason_switch_is_read_as_the_file_sets_it(tmp_path):
    _write(tmp_path, MODELS + DECIDE.replace("choice_reasons = true", "choice_reasons = false") + JEV)

    assert settings.load(tmp_path).choice_reasons is False


class _ClaudeThenModel:
    """Stands in for Claude, which refuses every request, and the model server, which answers with a reason."""

    def __init__(self) -> None:
        self.bodies: dict[str, dict] = {}
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.bodies[request.url.host] = json.loads(request.content)
        if request.url.host == "api.anthropic.com":
            return httpx.Response(500)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"choice": "HOLD", "reason": REASON})}}]}
        )


@pytest.mark.parametrize(
    "switch, local_tokens, claude_tokens, schema_keys",
    [("true", 64, 56, {"choice", "reason"}), ("false", 24, 16, {"choice"})],
    ids=["on", "off"],
)
def test_reason_a_chain_s_claude_and_local_sources_ask_for_a_reason_as_the_switch_says(
    run_folder, match_record, monkeypatch, switch, local_tokens, claude_tokens, schema_keys
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", CLAUDE_KEY)
    config = run_folder / "config"
    config.mkdir()
    chain = '\n[decide.chains]\nplan = ["claude:sonnet", "local:fast"]\n'
    agent_toml = (
        MODELS + DECIDE.replace("true", switch) + chain + JEV + CLAUDE.replace("enabled = false", "enabled = true")
    )
    (config / "agent.toml").write_bytes(agent_toml.encode("utf-8"))
    (config / "models.toml").write_bytes(MODELS_TOML.encode("utf-8"))
    services = _ClaudeThenModel()

    result = build_chain("plan", transport=services.transport).choose(REQUEST)

    claude_body, local_body = services.bodies["api.anthropic.com"], services.bodies["127.0.0.1"]
    assert (claude_body["max_tokens"], local_body["max_tokens"]) == (claude_tokens, local_tokens)
    assert set(local_body["response_format"]["json_schema"]["schema"]["properties"]) == schema_keys
    assert (result.option_id, result.model_reason) == ("HOLD", REASON if switch == "true" else None)
    local_decision = [line["data"] for line in match_record() if line["kind"] == "decision"][-1]
    assert local_decision.get("model_reason") == (REASON if switch == "true" else None)
    assert ("model_reason" in local_decision) is (switch == "true")


def test_reason_jev_is_asked_as_it_is_without_the_switch(key, run_folder):
    _write(run_folder, MODELS + DECIDE + '\n[decide.chains]\nplan = ["jev"]\n' + JEV.replace("false", "true"))
    services = _Services()

    build_chain("plan", transport=services.transport).choose(REQUEST)

    (request,) = services.requests
    assert json.loads(request.content)["output_schema"] == {
        "type": "object",
        "properties": {"choice": {"enum": ["HOLD", "RAID"]}},
        "required": ["choice"],
    }


# -- building a chain ------------------------------------------------------------------------------


def test_a_chain_asks_its_entries_in_order_and_is_named_after_its_key(key, run_folder):
    _write(run_folder, MODELS + DECIDE + CHAINS + JEV.replace("enabled = false", "enabled = true"))
    services = _Services(jev_confidence=0.3)

    chain = build_chain("red_alert_plan", transport=services.transport)
    result = chain.choose(REQUEST)

    assert chain.name == "red_alert_plan"
    assert services.urls() == ["https://api.jevai.net/v1/decide", "http://127.0.0.1:11777/v1/chat/completions"]
    assert (result.option_id, result.source, result.outcome) == ("HOLD", "local:fast", "used")


def test_record_a_chain_s_local_source_records_the_reasoning_setting_the_models_file_gives_its_role(
    run_folder, match_record
):
    strong_reasons = MODELS_TOML.replace(
        '16384\ntemperature = 0\nreasoning = "off"', '16384\ntemperature = 0\nreasoning = "on"'
    )
    _write(run_folder, MODELS + DECIDE + CHAINS + JEV, models_toml=strong_reasons)
    services = _Services()

    build_chain("werewolf_vote", transport=services.transport).choose(REQUEST)
    build_chain("red_alert_plan", transport=services.transport).choose(REQUEST)

    decisions = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert [(data["source"], data["io"]["params"]["reasoning"]) for data in decisions] == [
        ("local:strong", "on"),
        ("local:fast", "off"),
    ]


def test_jev_is_left_out_of_a_chain_while_it_is_not_enabled(key, run_folder):
    _write(run_folder, MODELS + DECIDE + CHAINS + JEV)
    services = _Services()

    result = build_chain("red_alert_plan", transport=services.transport).choose(REQUEST)

    assert services.urls() == ["http://127.0.0.1:11777/v1/chat/completions"]
    assert (result.option_id, result.source) == ("HOLD", "local:fast")


def test_a_chain_of_nothing_but_a_disabled_jev_asks_nobody(key, run_folder):
    _write(run_folder, MODELS + DECIDE + '\n[decide.chains]\nplan = ["jev"]\n' + JEV)
    services = _Services()

    result = build_chain("plan", transport=services.transport).choose(REQUEST)

    assert services.requests == []
    assert (result.option_id, result.source, result.outcome, result.reason) == (None, "plan", "error", "no_source")


def test_an_enabled_jev_gets_the_base_url_the_minimum_confidence_and_the_field_names_of_the_file(key, run_folder):
    jev_table = """
[jev]
enabled = true
base_url = "https://jev.example.test"
min_confidence = 0.95

[jev.fields]
state = "input"
output_schema = "schema"
"""
    _write(run_folder, MODELS + DECIDE + '\n[decide.chains]\nplan = ["jev"]\n' + jev_table)
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"value": {"choice": "RAID"}, "confidence": 0.9})

    result = build_chain("plan", transport=httpx.MockTransport(answer)).choose(REQUEST)

    assert str(seen[0].url) == "https://jev.example.test/v1/decide"
    assert set(json.loads(seen[0].content)) == {"input", "schema"}
    assert (result.outcome, result.reason, result.confidence) == ("invalid", "low_confidence", 0.9)


def test_a_chain_the_file_does_not_name_cannot_be_built(run_folder):
    path = _write(run_folder, MODELS + DECIDE + CHAINS + JEV)

    with pytest.raises(ConfigError) as raised:
        build_chain("pokemon_draft")

    assert str(raised.value) == f"{path}: [decide.chains] has no chain named pokemon_draft."


def test_a_chain_that_names_a_role_the_models_file_does_not_define_cannot_be_built(run_folder):
    path = _write(run_folder, MODELS + DECIDE + '\n[decide.chains]\nplan = ["local:tiny"]\n' + JEV)

    with pytest.raises(ConfigError) as raised:
        build_chain("plan")

    assert str(raised.value) == (
        f"{path}: the chain plan names the role tiny, which config/models.toml does not define."
    )


def test_a_chain_with_a_local_entry_needs_the_models_file(run_folder):
    _write(run_folder, MODELS + DECIDE + '\n[decide.chains]\nplan = ["local:fast"]\n' + JEV, models_toml=None)

    with pytest.raises(ConfigError, match="models.toml was not found"):
        build_chain("plan")
