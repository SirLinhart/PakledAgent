"""`SystemoneSource`: the one choice question it puts to a native systemone server, how it reads the answer, what
fails as a source, and the `systemone:<name>` chain entries and `[systemone.<name>]` tables that name it."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from ucla_agent.core.config import ConfigError
from ucla_agent.core.decide import ChainSource, ChoiceRequest, Option, SystemoneSource, build_chain, settings
from ucla_agent.core.decide.settings import SystemoneSettings
from ucla_agent.core.decide.systemone import build_body
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.modelserver.systemone import SystemoneRole

REPO_ROOT = Path(__file__).resolve().parents[2]
STUB = REPO_ROOT / "tests" / "stubs" / "fake_systemone_server.py"
BASE_URL = "http://127.0.0.1:41777"
OPTIONS = [Option("D", "hold at the staging point"), Option("H", "attack the enemy units at (52,68)")]
SLOW_ANSWER_SECONDS = 0.4
AGENT_TOML = """
[models]
autostart = false

[decide]
default_deadline_ms = 1500
choice_reasons = true

[decide.chains]
red_alert_commander = ["systemone:jev9b"]

[jev]
enabled = false
base_url = "https://api.jevai.net"
min_confidence = 0.55

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
backend_port_base = 41700

[roles.small]
model_path = "C:/Models/gguf/small.gguf"
required = false
ctx_size = 8192
temperature = 0
reasoning = "off"

[systemone_roles.jev9b]
python_path = "C:/native/python.exe"
script = "scripts/serve_jev9b.py"
model_dir = "E:/Models/JevK5-9B"
required = false
"""


def _request(
    deadline_ms: int = 1500, system_text: str | None = "You command one group.", context: str = "t=06:00 army: 8"
) -> ChoiceRequest:
    return ChoiceRequest("ACTIONS:", OPTIONS, context, deadline_ms, "1250", system_text=system_text)


def _answer(choice: Any = "H", confidence: Any = 0.82) -> httpx.Response:
    answer = {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {"D": 0.1, "H": 0.9}}
    return httpx.Response(200, json={"model": "JevK5-9B", "answers": {"action": answer}, "latency_ms": 48.1})


class _Server:
    """Stands in for the systemone server: remembers each request and answers as the test says."""

    def __init__(self, answer: httpx.Response | Exception | None = None, seconds: float = 0.0) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer if answer is not None else _answer()
        self._seconds = seconds
        self.transport = httpx.MockTransport(self._respond)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        time.sleep(self._seconds)
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer

    def source(self, **options: Any) -> SystemoneSource:
        return SystemoneSource("jev9b", base_url=BASE_URL, transport=self.transport, **options)


# -- the request -----------------------------------------------------------------------------------


def test_the_request_goes_to_the_systemone_route_of_the_base_url():
    server = _Server()

    server.source().choose(_request())

    (request,) = server.requests
    assert (request.method, str(request.url)) == ("POST", f"{BASE_URL}/v1/systemone")
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in request.headers


def test_the_body_is_one_choice_question_with_the_context_as_state_and_the_options_as_criteria_once():
    server = _Server()

    server.source().choose(_request())

    assert json.loads(server.requests[0].content) == {
        "state": "t=06:00 army: 8",
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "You command one group.\nACTIONS:",
                "criteria": {"D": "hold at the staging point", "H": "attack the enemy units at (52,68)"},
            }
        },
    }


def test_a_request_carrying_a_state_object_sends_it_as_the_state_in_place_of_the_text():
    state = {"bases": [{"id": "E2", "kind": "expansion", "pos": [48, 84]}], "score": {"lead": 1450}}
    request = dataclasses.replace(_request(), state_object=state)
    server = _Server()

    server.source().choose(request)

    body = json.loads(server.requests[0].content)
    assert body["state"] == state and body["questions"]["action"]["instructions"] == "You command one group.\nACTIONS:"
    assert build_body(_request())["state"] == "t=06:00 army: 8"


def test_the_criteria_keep_the_order_of_the_options_whatever_their_letters():
    options = [Option("D", "gather"), Option("A", "pull back"), Option("X", "attack enemy expansion")]
    request = ChoiceRequest("ACTIONS:", options, "t=06:00", 1500, "1250")

    assert list(build_body(request)["questions"]["action"]["criteria"]) == ["D", "A", "X"]


def test_without_a_system_text_the_instructions_are_the_question_alone():
    body = build_body(_request(system_text=None))

    assert body["questions"]["action"]["instructions"] == "ACTIONS:"
    assert "hold at the staging point" not in body["state"]


# -- the answer ------------------------------------------------------------------------------------


def test_the_choice_and_confidence_become_the_result_with_no_model_reason():
    result = _Server(_answer("H", 0.82)).source().choose(_request())

    assert (result.option_id, result.confidence, result.outcome, result.source) == (
        "H",
        0.82,
        "used",
        "systemone:jev9b",
    )
    assert result.model_reason is None


def test_by_default_any_confidence_is_used():
    result = _Server(_answer("D", 0.0)).source().choose(_request())

    assert (result.option_id, result.outcome) == ("D", "used")


@pytest.mark.parametrize("confidence, outcome, reason", [(0.39, "invalid", "low_confidence"), (0.4, "used", None)])
def test_a_choice_below_min_confidence_is_invalid_so_the_script_s_choice_is_played(confidence, outcome, reason):
    result = _Server(_answer("H", confidence)).source(min_confidence=0.4).choose(_request())

    assert (result.outcome, result.reason) == (outcome, reason)
    assert result.option_id == ("H" if outcome == "used" else None)


def test_a_choice_that_was_not_offered_is_invalid():
    result = _Server(_answer("Z")).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "invalid", "not_an_option")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"answers": {}}),
        httpx.Response(200, json={"answers": {"action": {"choice": "H"}}}),
        httpx.Response(200, json={"answers": {"action": {"choice": 7, "confidence": 0.8}}}),
        httpx.Response(200, json={"answers": {"action": {"choice": "H", "confidence": True}}}),
        httpx.Response(200, json=["H"]),
    ],
    ids=["not json", "no action", "no confidence", "choice not text", "confidence not a number", "a list"],
)
def test_an_answer_that_cannot_be_read_is_an_error(answer):
    result = _Server(answer).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", "malformed_answer")


@pytest.mark.parametrize("status", [422, 500, 503])
def test_a_refusal_such_as_422_is_an_error_with_its_status(status):
    refusal = httpx.Response(status, json={"error": {"code": "input_budget_exceeded", "message": "too long"}})

    result = _Server(refusal).source().choose(_request())

    assert (result.option_id, result.outcome, result.reason) == (None, "error", f"http_{status}")


def test_a_server_that_cannot_be_reached_is_an_error_and_not_an_exception():
    result = _Server(httpx.ConnectError("refused")).source().choose(_request())

    assert (result.outcome, result.reason) == ("error", "connection_failed")


def test_an_answer_slower_than_the_deadline_is_a_timeout_at_the_deadline():
    started = time.perf_counter()

    result = _Server(seconds=SLOW_ANSWER_SECONDS).source().choose(_request(deadline_ms=100))

    assert (result.outcome, result.reason) == ("timeout", "deadline")
    assert time.perf_counter() - started < SLOW_ANSWER_SECONDS


# -- the record ------------------------------------------------------------------------------------


def test_record_io_holds_the_request_body_as_sent_and_the_whole_answer_as_it_came_back(match_record):
    response_body = (
        '{"answers": {"action": {"choice": "H", "confidence": 0.82, "probabilities": {"D": 0.1, "H": 0.9}}}}'
    )
    server = _Server(httpx.Response(200, text=response_body))

    server.source(min_confidence=0.3).choose(_request(deadline_ms=900))

    (decision,) = [line["data"] for line in match_record() if line["kind"] == "decision"]
    assert (decision["source"], decision["answer"], decision["confidence"], decision["outcome"]) == (
        "systemone:jev9b",
        "H",
        0.82,
        "used",
    )
    assert "model_reason" not in decision
    assert decision["io"] == {
        "system": None,
        "user": server.requests[0].content.decode("utf-8"),
        "response_format": None,
        "params": {"source": "systemone:jev9b", "min_confidence": 0.3, "deadline_ms": 900},
        "raw_output": response_body,
        "reasoning_text": None,
    }


def test_record_a_choice_below_min_confidence_falls_back_to_the_script(match_record):
    _Server(_answer("H", 0.2)).source(min_confidence=0.5).choose(_request())

    events = [(line["kind"], line["data"]) for line in match_record() if line["kind"] in ("decision", "fallback")]
    (_, decision), (_, fallback) = events
    assert (decision["outcome"], decision["reason"], decision["io"]["raw_output"]) == (
        "invalid",
        "low_confidence",
        _answer("H", 0.2).text,
    )
    assert fallback == {"intended_source": "systemone:jev9b", "reason": "invalid", "used": "script"}


# -- a question with one option -------------------------------------------------------------------


class _RefusingOneOption(_Server):
    """Stands in for the jev-style server, which refuses a choice whose criteria name fewer than two options."""

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(json.loads(request.content)["questions"]["action"]["criteria"]) < 2:
            return httpx.Response(500, json={"detail": "choice criteria must name at least two options"})
        return _answer()


def test_the_stand_in_refuses_a_one_option_question_asked_of_the_source_directly():
    result = _RefusingOneOption().source().choose(dataclasses.replace(_request(), options=OPTIONS[:1]))

    assert (result.outcome, result.reason) == ("error", "http_500")


def test_a_one_option_question_never_reaches_a_server_that_refuses_it_and_its_circuit_stays_closed(run_folder):
    server = _RefusingOneOption()
    chain = ChainSource([server.source()], name="red_alert_commander")
    one_option = dataclasses.replace(_request(), options=OPTIONS[:1])

    answers = {
        (result.option_id, result.confidence, result.outcome, result.reason)
        for result in [chain.choose(one_option) for _ in range(10)]
    }
    after = chain.choose(_request())

    assert answers == {("D", 1.0, "used", "single_option")}
    (request,) = server.requests
    assert list(json.loads(request.content)["questions"]["action"]["criteria"]) == ["D", "H"]
    assert (after.option_id, after.source, after.outcome) == ("H", "systemone:jev9b", "used")


# -- a stub server over HTTP -----------------------------------------------------------------------


def test_a_stub_systemone_server_answers_a_choice_and_the_source_reads_it(tmp_path, model_ports, monkeypatch):
    monkeypatch.setenv("STUB_CHOICE", "H")
    monkeypatch.setenv("STUB_CONFIDENCE", "0.77")
    port = model_ports.backend_port(0)
    role_settings = server_settings.SystemoneRoleSettings(
        "jev9b", Path(sys.executable), STUB, tmp_path, False, 30, {}, 0
    )
    role = SystemoneRole(role_settings, port, tmp_path / "jev9b.log")
    role.start()
    try:
        result = SystemoneSource("jev9b", base_url=f"http://127.0.0.1:{port}").choose(_request(deadline_ms=5000))
    finally:
        role.stop()

    assert role.reason == "stopped"
    assert (result.option_id, result.confidence, result.outcome) == ("H", 0.77, "used")


# -- chain entries and settings --------------------------------------------------------------------


def _write(root: Path, agent_toml: str = AGENT_TOML, models_toml: str = MODELS_TOML) -> None:
    config = root / "config"
    config.mkdir(exist_ok=True)
    (config / "agent.toml").write_bytes(agent_toml.encode("utf-8"))
    (config / "models.toml").write_bytes(models_toml.encode("utf-8"))


def test_a_systemone_entry_asks_the_systemone_role_of_its_name_on_that_role_s_own_port(run_folder):
    _write(run_folder)
    server = _Server()

    result = build_chain("red_alert_commander", transport=server.transport).choose(_request())

    assert [str(request.url) for request in server.requests] == ["http://127.0.0.1:41701/v1/systemone"]
    assert (result.source, result.option_id) == ("systemone:jev9b", "H")


def test_the_systemone_role_s_port_moves_with_the_backend_port_base_of_the_environment(run_folder, monkeypatch):
    _write(run_folder)
    monkeypatch.setenv(server_settings.BACKEND_PORT_BASE_ENV, "41600")
    server = _Server()

    build_chain("red_alert_commander", transport=server.transport).choose(_request())

    assert [str(request.url) for request in server.requests] == ["http://127.0.0.1:41601/v1/systemone"]


def test_a_systemone_table_may_give_its_own_loopback_base_url_and_min_confidence(run_folder):
    _write(run_folder, AGENT_TOML + '\n[systemone.jev9b]\nbase_url = "http://localhost:8790"\nmin_confidence = 0.5\n')
    server = _Server(_answer("H", 0.45))

    result = build_chain("red_alert_commander", transport=server.transport).choose(_request())

    assert [str(request.url) for request in server.requests] == ["http://localhost:8790/v1/systemone"]
    assert (result.outcome, result.reason) == ("invalid", "low_confidence")
    assert settings.load(run_folder).systemone == {"jev9b": SystemoneSettings("http://localhost:8790", 0.5)}


@pytest.mark.parametrize(
    "table, expected",
    [
        ('base_url = "http://192.168.1.20:8765"', "base_url in [systemone.jev9b] must be an http URL of a loopback"),
        ('base_url = "https://127.0.0.1:8765"', "base_url in [systemone.jev9b] must be an http URL of a loopback"),
        ('base_url = "127.0.0.1:8765"', "base_url in [systemone.jev9b] must be an http URL of a loopback"),
        ("min_confidence = 1.5", "min_confidence in [systemone.jev9b] must be a number from 0 to 1"),
        ("port = 8765", "has no setting named port"),
    ],
    ids=["another machine", "https", "no scheme", "confidence over 1", "unknown key"],
)
def test_a_systemone_table_is_read_strictly(tmp_path, table, expected):
    _write(tmp_path, AGENT_TOML + f"\n[systemone.jev9b]\n{table}\n")

    with pytest.raises(ConfigError, match=re.escape(expected)):
        settings.load(tmp_path)


def test_a_systemone_entry_without_a_role_or_a_base_url_cannot_be_built(run_folder):
    _write(run_folder, AGENT_TOML.replace("systemone:jev9b", "systemone:jev27b"))

    with pytest.raises(ConfigError, match="names the systemone server jev27b"):
        build_chain("red_alert_commander")


def test_a_local_entry_cannot_name_a_systemone_role(run_folder):
    _write(run_folder, AGENT_TOML.replace("systemone:jev9b", "local:jev9b"))

    with pytest.raises(ConfigError, match="names the role jev9b"):
        build_chain("red_alert_commander")


def test_systemone_is_a_chain_entry_kind_but_needs_a_name(tmp_path):
    _write(tmp_path, AGENT_TOML.replace('["systemone:jev9b"]', '["systemone:"]'))

    with pytest.raises(ConfigError, match='"systemone:<name>"'):
        settings.load(tmp_path)


def test_the_shipped_red_alert_chains_ask_jev9b_on_its_own_port(run_folder):
    shutil.copytree(REPO_ROOT / "config", run_folder / "config")
    shipped = settings.load(run_folder)
    server = _Server()

    result = build_chain("red_alert_commander", transport=server.transport).choose(_request())

    assert {name: shipped.chains[name] for name in ("red_alert_plan", "red_alert_scout", "red_alert_commander")} == (
        dict.fromkeys(("red_alert_plan", "red_alert_scout", "red_alert_commander"), ("systemone:jev9b",))
    )
    assert shipped.systemone == {"jev9b": SystemoneSettings(None, 0.0)}
    assert [str(request.url) for request in server.requests] == ["http://127.0.0.1:41503/v1/systemone"]
    assert (result.source, result.outcome) == ("systemone:jev9b", "used")
