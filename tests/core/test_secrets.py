"""Secrets: loading them, redacting them, binding a client to its service and scanning the tracked files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from altruagent import DecisionContext
from ucla_agent.core import records, secrets
from ucla_agent.core.secrets import REDACTED, BoundClient, SecretDestinationError, redact

REPO_ROOT = Path(__file__).resolve().parents[2]

# Secret-shaped strings are put together at run time, so that this file holds
# no literal the tracked-file scan would report.
AGENT_KEY = "eak_" + "live_" + "abcdefgh12345678"
CLAIM_CODE = "seat" + "claim_" + "Zx9-41_bQ7"
PLATFORM_KEY = "sk_" + "agent_" + "0123456789abcdef"
JWT = ".".join(("eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJhZ2VudCJ9", "c2lnbmF0dXJl"))
LOADED_KEY = "jev-4f9c2d7e1a6b8053"
JEV_BASE_URL = "https://api.jevai.net"
MODEL_SERVER_URL = "http://127.0.0.1:11500"
SECRET_VARIABLE_NAMES = [
    "ALTRUAGENT_OFFICIAL_AGENT_KEY",
    "ALTRUAGENT_CLAIM_TOKEN",
    "JEV_API_KEY",
    "OPENAI_API_KEY",
    "SIGNING_KEY",
    "HF_TOKEN",
    "CLIENT_SECRET",
]


@pytest.fixture
def forget_variable(monkeypatch):
    """Removes an environment variable now and again after the test, whatever the test loads into it meanwhile."""

    def forget(name: str) -> None:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)

    return forget


# -- redact ----------------------------------------------------------------------------------------


def test_redact_replaces_a_loaded_key_value(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)

    assert redact(f"Authorization: Bearer {LOADED_KEY} was sent") == f"Authorization: Bearer {REDACTED} was sent"


@pytest.mark.parametrize(
    "secret", [AGENT_KEY, CLAIM_CODE, PLATFORM_KEY, JWT], ids=["agent key", "claim code", "platform key", "jwt"]
)
def test_redact_replaces_a_secret_shaped_string_inside_a_longer_text(run_folder, secret):
    assert redact(f"the platform answered {secret}, then closed") == f"the platform answered {REDACTED}, then closed"


def test_redact_leaves_text_without_a_secret_unchanged(run_folder):
    text = 'tick 1500: {"cmd": "build", "item": "powr"} placed at [22, 48]; keys stay in .env'

    assert redact(text) == text


def test_redact_replaces_every_occurrence(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)

    redacted = redact(f"{LOADED_KEY} and {JWT} and {LOADED_KEY} again")

    assert redacted == f"{REDACTED} and {REDACTED} and {REDACTED} again"


@pytest.mark.parametrize("name", SECRET_VARIABLE_NAMES)
def test_a_variable_with_a_secret_name_is_redacted(run_folder, monkeypatch, name):
    monkeypatch.setenv(name, "value-0123456789")

    assert redact("sent value-0123456789 along") == f"sent {REDACTED} along"


def test_a_variable_without_a_secret_name_is_not_redacted(run_folder, monkeypatch):
    monkeypatch.setenv("ALTRUAGENT_CONTROL_URL", "https://control.example.test")

    assert redact("connecting to https://control.example.test") == "connecting to https://control.example.test"


def test_a_secret_shorter_than_eight_characters_is_not_redacted(run_folder, monkeypatch):
    monkeypatch.setenv("SHORT_TOKEN", "abc1234")
    monkeypatch.setenv("LONG_ENOUGH_TOKEN", "abc12345")

    assert redact("abc1234 / abc12345") == f"abc1234 / {REDACTED}"


def test_a_value_is_redacted_in_the_form_json_text_holds_it(run_folder, monkeypatch):
    quoted_value = 'pass"word\\with-escapes'
    monkeypatch.setenv("SERVICE_SECRET", quoted_value)

    line = redact(json.dumps({"prompt": f"use {quoted_value} now"}))

    assert json.loads(line) == {"prompt": f"use {REDACTED} now"}


def test_the_longest_loaded_value_is_replaced_whole(run_folder, monkeypatch):
    monkeypatch.setenv("FIRST_KEY", "abcdefgh")
    monkeypatch.setenv("SECOND_KEY", "abcdefgh-and-more")

    assert redact("x abcdefgh-and-more y abcdefgh z") == f"x {REDACTED} y {REDACTED} z"


def test_a_loaded_value_that_starts_like_a_key_is_replaced_whole(run_folder, monkeypatch):
    longer_than_the_shape = AGENT_KEY + ".tail+more"
    monkeypatch.setenv("WRAPPED_KEY", longer_than_the_shape)

    assert redact(f"<{longer_than_the_shape}>") == f"<{REDACTED}>"


@pytest.mark.parametrize(
    "name, expected",
    [("jev_api_key", True), ("my_service_token", True), ("KEYBOARD", False), ("TOKEN_COUNT", False), ("PATH", False)],
)
def test_secret_names_are_recognized_in_any_letter_case(name, expected):
    assert secrets.is_secret_name(name) is expected


# -- loading ---------------------------------------------------------------------------------------


def test_load_secrets_reads_the_env_file_of_the_repository_root_into_the_environment(run_folder, forget_variable):
    forget_variable("JEV_API_KEY")
    (run_folder / ".env").write_text(f"JEV_API_KEY={LOADED_KEY}\n", encoding="utf-8")

    secrets.load_secrets()

    assert os.environ["JEV_API_KEY"] == LOADED_KEY
    assert redact(f"key {LOADED_KEY}") == f"key {REDACTED}"


def test_redact_loads_the_env_file_by_itself_on_first_use(run_folder, forget_variable):
    forget_variable("JEV_API_KEY")
    (run_folder / ".env").write_text(f"JEV_API_KEY={LOADED_KEY}\n", encoding="utf-8")

    assert redact(f"key {LOADED_KEY}") == f"key {REDACTED}"


def test_a_variable_that_is_already_set_keeps_its_value(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "set-before-0123456789")
    (run_folder / ".env").write_text(f"JEV_API_KEY={LOADED_KEY}\n", encoding="utf-8")

    secrets.load_secrets()

    assert os.environ["JEV_API_KEY"] == "set-before-0123456789"
    assert redact("set-before-0123456789") == REDACTED


def test_loading_again_picks_up_a_changed_environment(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "first-value-0123")
    secrets.load_secrets()
    monkeypatch.setenv("JEV_API_KEY", "second-value-4567")

    secrets.load_secrets()

    assert redact("first-value-0123 second-value-4567") == f"first-value-0123 {REDACTED}"


def test_loading_without_an_env_file_still_redacts_the_environment(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)

    secrets.load_secrets()

    assert not (run_folder / ".env").exists()
    assert redact(LOADED_KEY) == REDACTED


# -- a client bound to one service -----------------------------------------------------------------


class _StubService:
    """Stands in for the network: remembers every request that would have been sent and answers 200."""

    def __init__(self, status_code: int = 200, headers: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._status_code = status_code
        self._headers = headers
        self.transport = httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status_code, headers=self._headers, json={"choice": "HOLD"})


def _jev_client(service: _StubService) -> BoundClient:
    return BoundClient(JEV_BASE_URL, headers={"Authorization": f"Bearer {LOADED_KEY}"}, transport=service.transport)


def test_a_model_prompt_holding_a_loaded_key_reaches_the_transport_redacted(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)
    service = _StubService()
    client = BoundClient(MODEL_SERVER_URL, transport=service.transport)
    prompt = f"Choose a plan. Context: the operator pasted {LOADED_KEY} and {AGENT_KEY} here."

    answer = client.request(
        "POST",
        "/v1/chat/completions",
        json_body={"model": "fast", "messages": [{"role": "user", "content": prompt}]},
        timeout=1.0,
    )

    (request,) = service.requests
    sent_text = request.content.decode("utf-8")
    assert LOADED_KEY not in sent_text and AGENT_KEY not in sent_text
    assert json.loads(sent_text)["messages"][0]["content"] == (
        f"Choose a plan. Context: the operator pasted {REDACTED} and {REDACTED} here."
    )
    assert request.headers["content-type"] == "application/json"
    assert str(request.url) == f"{MODEL_SERVER_URL}/v1/chat/completions"
    assert answer.json() == {"choice": "HOLD"}


def test_a_jev_client_refuses_to_call_another_host(run_folder):
    service = _StubService()
    client = _jev_client(service)

    with pytest.raises(SecretDestinationError) as refused:
        client.request("POST", "https://example.com/v1/decide", json_body={"question": "plan"}, timeout=1.0)

    assert service.requests == []
    assert "api.jevai.net" in str(refused.value) and "example.com" in str(refused.value)
    assert LOADED_KEY not in str(refused.value)


@pytest.mark.parametrize(
    "url",
    ["http://api.jevai.net/v1/decide", "https://api.jevai.net:8443/v1/decide", "//example.com/v1/decide"],
    ids=["another scheme", "another port", "scheme-relative host"],
)
def test_a_client_refuses_any_destination_but_its_own_scheme_host_and_port(run_folder, url):
    service = _StubService()

    with pytest.raises(SecretDestinationError):
        _jev_client(service).request("POST", url, json_body={}, timeout=1.0)

    assert service.requests == []


@pytest.mark.parametrize("url", ["/v1/decide", "https://api.jevai.net/v1/decide"], ids=["path", "absolute url"])
def test_the_credential_goes_to_the_service_the_client_was_built_for(run_folder, url):
    service = _StubService()

    _jev_client(service).request("POST", url, json_body={"question": "plan"}, timeout=1.0)

    (request,) = service.requests
    assert str(request.url) == "https://api.jevai.net/v1/decide"
    assert request.headers["authorization"] == f"Bearer {LOADED_KEY}"


def test_a_redirect_to_another_host_is_returned_and_not_followed(run_folder):
    service = _StubService(status_code=302, headers={"Location": "https://example.com/v1/decide"})

    answer = _jev_client(service).request("POST", "/v1/decide", json_body={}, timeout=1.0)

    assert answer.status_code == 302
    assert [str(request.url) for request in service.requests] == ["https://api.jevai.net/v1/decide"]


def test_a_request_without_a_body_sends_none(run_folder):
    service = _StubService()
    client = BoundClient(MODEL_SERVER_URL, transport=service.transport)

    client.request("GET", "/health", timeout=1.0)
    client.close()

    (request,) = service.requests
    assert (request.method, request.content) == ("GET", b"")
    assert "content-type" not in request.headers


def test_a_proxy_named_in_the_environment_is_not_used(run_folder, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.test:3128")

    client = BoundClient(JEV_BASE_URL, headers={"Authorization": f"Bearer {LOADED_KEY}"})
    uses_the_environment = client._http.trust_env
    client.close()

    assert uses_the_environment is False


# -- what the record writer puts on disk -----------------------------------------------------------


def test_a_record_whose_payload_holds_a_loaded_key_is_written_redacted(run_folder, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)
    context = DecisionContext(session_id="session-1", tournament_id=None, game_type="red_alert", agent_id="agent-1")
    records.start_match(context, None)

    records.log("decision", {"source": "jev", "request_summary": f"sent with {LOADED_KEY}", "answer": AGENT_KEY})
    records.finish_match({"returns": {"agent-1": 1.0}})

    (events_path,) = run_folder.glob("runs/*/session-1__seat/events.jsonl")
    written = events_path.read_text(encoding="utf-8")
    assert LOADED_KEY not in written and AGENT_KEY not in written
    assert written.count(REDACTED) == 2
    assert [json.loads(line)["kind"] for line in written.splitlines()] == ["decision", "result"]


def test_a_run_line_is_written_redacted(run_folder):
    records.append_run_line("run_started", {"flags": ["--claim", CLAIM_CODE]})

    (run_path,) = run_folder.glob("runs/*/run.jsonl")
    written = run_path.read_text(encoding="utf-8")
    assert CLAIM_CODE not in written
    assert json.loads(written)["data"] == {"flags": ["--claim", REDACTED]}


# -- the tracked-file scan -------------------------------------------------------------------------


def _track(git, root: Path, relative_path: str, content: str | bytes) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    git(root, "add", "--", relative_path)


def test_the_scan_exits_1_on_a_tracked_agent_key_and_0_once_the_file_is_removed(git_repo, git, run_script):
    _track(git, git_repo, "notes/leak.txt", f"first line\nsecond line\nkey = {AGENT_KEY}\n")

    with_the_file = run_script(git_repo, "scan_secrets.py")
    git(git_repo, "rm", "--quiet", "--force", "notes/leak.txt")
    without_the_file = run_script(git_repo, "scan_secrets.py")

    assert (with_the_file.returncode, with_the_file.stdout) == (1, "notes/leak.txt:3\n")
    assert AGENT_KEY not in with_the_file.stdout + with_the_file.stderr
    assert (without_the_file.returncode, without_the_file.stdout) == (0, "")


def test_a_tracked_file_deleted_from_the_working_tree_is_skipped(git_repo, git, load_script, capsys):
    _track(git, git_repo, "leak.txt", AGENT_KEY)
    (git_repo / "leak.txt").unlink()

    exit_code = load_script("scan_secrets.py").main()

    assert (exit_code, capsys.readouterr().out) == (0, "")


def test_the_scan_reports_each_secret_shape_by_path_and_line(git_repo, git, load_script, capsys):
    lines = ["nothing here", CLAIM_CODE, "nothing here either", f"a {PLATFORM_KEY} and a {JWT} on one line"]
    _track(git, git_repo, "docs/shapes.md", "\n".join(lines))
    _track(git, git_repo, "src/key.py", f'KEY = "{AGENT_KEY}"\n')

    exit_code = load_script("scan_secrets.py").main()

    assert exit_code == 1
    assert capsys.readouterr().out.splitlines() == ["docs/shapes.md:2", "docs/shapes.md:4", "src/key.py:1"]


def test_the_scan_reports_a_secret_value_of_the_current_env_file(git_repo, git, load_script, capsys):
    env_lines = [f"JEV_API_KEY={LOADED_KEY}", "ALTRUAGENT_CONTROL_URL=https://control.example.test", ""]
    (git_repo / ".env").write_text("\n".join(env_lines), encoding="utf-8")
    tracked_lines = ["[jev]", 'base_url = "https://control.example.test"', f'key = "{LOADED_KEY}"', ""]
    _track(git, git_repo, "config/jev.toml", "\n".join(tracked_lines))

    exit_code = load_script("scan_secrets.py").main()

    assert (exit_code, capsys.readouterr().out) == (1, "config/jev.toml:3\n")


def test_an_untracked_file_is_not_scanned(git_repo, load_script, capsys):
    (git_repo / "scratch.txt").write_text(AGENT_KEY, encoding="utf-8")

    exit_code = load_script("scan_secrets.py").main()

    assert (exit_code, capsys.readouterr().out) == (0, "")


def test_a_tracked_file_that_is_not_text_is_still_scanned(git_repo, git, load_script, capsys):
    not_utf8 = bytes([0xFF, 0xFE, 0x00])
    _track(git, git_repo, "model.bin", not_utf8 + b"binary\n" + AGENT_KEY.encode("ascii") + not_utf8)

    exit_code = load_script("scan_secrets.py").main()

    assert (exit_code, capsys.readouterr().out) == (1, "model.bin:2\n")


def test_outside_a_git_repository_the_scan_exits_2(run_folder, monkeypatch, load_script, capsys):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(run_folder.parent))

    exit_code = load_script("scan_secrets.py").main()

    captured = capsys.readouterr()
    assert (exit_code, captured.out) == (2, "")
    assert captured.err.startswith("Could not list the files git tracks in ")


def test_no_file_tracked_in_this_repository_holds_a_secret(load_script):
    assert load_script("scan_secrets.py").find_hits(REPO_ROOT) == []


# -- what git must never track ---------------------------------------------------------------------


@pytest.mark.parametrize("entry", [".env", "runs/", "tmp/", "docs/reference/research/"])
def test_gitignore_lists_the_folders_and_files_that_hold_secrets_or_local_material(entry):
    assert entry in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_env_template_names_every_secret_the_agent_reads_and_gives_none_a_value():
    template = dotenv_values(REPO_ROOT / ".env.example")
    secret_names = {name for name in template if secrets.is_secret_name(name)}

    assert secret_names == {"ALTRUAGENT_OFFICIAL_AGENT_KEY", "JEV_API_KEY", "ANTHROPIC_API_KEY"}
    assert [name for name, value in template.items() if value] == []
    assert secrets.secret_values(template) == []
