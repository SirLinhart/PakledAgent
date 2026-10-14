"""The readiness checks that reach a service: platform, model_server and jev, against stubs and local stand-ins."""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from altruagent.errors import AltruAgentError, ConfigurationError, PlatformError
from altruagent.official import OfficialAgentError
from ucla_agent.check import services
from ucla_agent.check.line import FAIL, PASS, SKIP, WARN, CheckLine
from ucla_agent.core import modelserver
from ucla_agent.core.console import print_line
from ucla_agent.core.decide import ERROR, TIMEOUT, USED, DecisionSource
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.core.modelserver import settings as server_settings

# Shaped like an Official Agent Key, and no real one.
WELL_FORMED_KEY = "eak_live_" + "0123456789abcdef" * 4


class StubClient:
    """The official client's three calls, each answering what a test says."""

    def __init__(self, sign_in: Exception | None = None, listing: Exception | None = None, found: int = 2) -> None:
        self._sign_in, self._listing, self._found = sign_in, listing, found
        self.closed = False

    def authenticate(self) -> None:
        if self._sign_in is not None:
            raise self._sign_in

    def assignments(self) -> list:
        if self._listing is not None:
            raise self._listing
        return [object()] * self._found

    def close(self) -> None:
        self.closed = True


# -- platform ---------------------------------------------------------------------------------------


def test_without_a_key_the_platform_is_a_warning_and_nothing_is_reached(monkeypatch):
    monkeypatch.delenv("ALTRUAGENT_OFFICIAL_AGENT_KEY")

    line = services.check_platform(lambda: pytest.fail("no client may be made without a key"))

    assert line == CheckLine(WARN, "platform", "no key set")


def test_a_reachable_platform_that_accepts_the_key_and_lists_the_assignments_passes():
    client = StubClient(found=2)

    line = services.check_platform(lambda: client)

    assert line == CheckLine(
        PASS, "platform", "control plane reachable, key accepted, assignment discovery works (2 active assignment(s))"
    )
    assert client.closed


@pytest.mark.parametrize(
    "client, said",
    [
        (
            StubClient(sign_in=OfficialAgentError("key revoked", status_code=401)),
            "control plane reachable, Official Agent Key rejected: key revoked",
        ),
        (
            StubClient(sign_in=OfficialAgentError("busy", status_code=503)),
            "control plane reachable, authentication failed: busy",
        ),
        (
            StubClient(sign_in=PlatformError("Could not reach the control plane", status_code=None)),
            "control plane not reachable: Could not reach the control plane",
        ),
        (
            StubClient(listing=AltruAgentError("server error")),
            "control plane reachable and key accepted, assignment discovery failed: server error",
        ),
    ],
    ids=["key rejected", "sign-in failed", "unreachable", "listing failed"],
)
def test_each_platform_step_that_fails_is_a_failure_naming_it(client, said):
    line = services.check_platform(lambda: client)

    assert line == CheckLine(FAIL, "platform", said) and client.closed


def test_a_key_the_client_cannot_use_fails_before_anything_is_sent():
    def refuse():
        raise ConfigurationError("ALTRUAGENT_OFFICIAL_AGENT_KEY doesn't look like an Official Agent Key")

    assert services.check_platform(refuse).status == FAIL


def test_the_real_client_refuses_a_key_of_the_wrong_shape_without_saying_it():
    # The suite-wide fixture sets the key to a value of the wrong shape.
    line = services.check_platform()

    assert line.status == FAIL and line.detail.startswith("the key cannot be used:")
    assert "not-an-agent-key" not in line.detail


def test_the_real_client_with_a_well_formed_key_reports_an_unreachable_control_plane(monkeypatch):
    # The suite-wide fixture points the control plane at a loopback port nothing listens on.
    monkeypatch.setenv("ALTRUAGENT_OFFICIAL_AGENT_KEY", WELL_FORMED_KEY)

    line = services.check_platform()

    assert line.status == FAIL and line.detail.startswith("control plane not reachable:")


# -- model_server -----------------------------------------------------------------------------------


def answering(failures: dict[str, str | None]):
    """A role probe that answers for every role as given, and remembers whether the agent would start the server."""
    seen = []

    def probe(settings, autostart):
        seen.append(([role.name for role in settings.roles], autostart))
        return failures

    probe.seen = seen
    return probe


def require(root, role: str) -> None:
    """Mark a role of the copied `config/models.toml` as required."""
    models = root / "config" / "models.toml"
    text = models.read_text(encoding="utf-8")
    table = f"[roles.{role}]"
    head, tail = text.split(table)
    models.write_text(head + table + tail.replace("required = false", "required = true", 1), encoding="utf-8")


def set_autostart(root, on: bool, roles: str | None = None) -> None:
    """Set autostart in the copied `config/agent.toml`, and autostart_roles as the file writes it unless None."""
    agent = root / "config" / "agent.toml"
    lines = [
        line
        for line in agent.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("autostart =", "autostart_roles ="))
    ]
    models = lines.index("[models]") + 1
    lines[models:models] = [f"autostart = {str(on).lower()}"] + (
        [] if roles is None else [f"autostart_roles = {roles}"]
    )
    agent.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_every_role_answering_passes(checked_project):
    set_autostart(checked_project, False)
    probe = answering({"fast": None, "strong": None, "small": None})

    line = services.check_model_server(checked_project, probe)

    assert line == CheckLine(PASS, "model_server", "fast, strong, small each answered a 1-token completion within 30 s")
    assert probe.seen == [(["fast", "strong", "small"], False)]


def test_an_optional_role_that_does_not_answer_is_a_warning(checked_project):
    set_autostart(checked_project, False)

    line = services.check_model_server(
        checked_project, answering({"fast": None, "strong": "no answer (ReadTimeout)", "small": None})
    )

    assert line == CheckLine(WARN, "model_server", "strong (optional) did not answer: no answer (ReadTimeout)")


def test_a_required_role_that_does_not_answer_is_a_failure(checked_project):
    set_autostart(checked_project, False)
    require(checked_project, "fast")

    line = services.check_model_server(
        checked_project, answering({"fast": "answered HTTP 503", "strong": None, "small": None})
    )

    assert line == CheckLine(FAIL, "model_server", "fast (required) did not answer: answered HTTP 503")


def raising(error: Exception):
    def probe(settings, autostart):
        raise error

    return probe


def test_a_server_that_breaks_in_an_unexpected_way_costs_optional_roles_only_a_warning(checked_project):
    set_autostart(checked_project, False)

    line = services.check_model_server(checked_project, raising(RuntimeError("out of memory")))

    assert line.status == WARN
    assert line.detail.count("did not answer: RuntimeError: out of memory") == 3


def test_a_server_that_breaks_with_a_required_role_is_a_failure(checked_project):
    set_autostart(checked_project, False)
    require(checked_project, "strong")

    line = services.check_model_server(checked_project, raising(RuntimeError("out of memory")))

    assert line.status == FAIL and "strong (required) did not answer" in line.detail


def test_a_required_role_the_server_cannot_bring_up_is_a_failure_in_the_server_s_words(checked_project):
    set_autostart(checked_project, False)
    unavailable = modelserver.RequiredModelUnavailable("Required model role not available: fast (model_file_missing).")

    line = services.check_model_server(checked_project, raising(unavailable))

    assert line == CheckLine(FAIL, "model_server", "Required model role not available: fast (model_file_missing).")


def test_without_autostart_no_server_runs_in_play_and_every_role_is_said_not_to_answer(checked_project):
    set_autostart(checked_project, False)

    line = services.check_model_server(checked_project)

    assert line.status == WARN and line.detail.count(services.AUTOSTART_OFF) == 3


def test_settings_that_cannot_be_read_are_a_failure(checked_project):
    (checked_project / "config" / "models.toml").write_text("[server]\n", encoding="utf-8")

    line = services.check_model_server(checked_project, answering({}))

    assert line.status == FAIL and line.detail.startswith("the model server's settings cannot be read:")


def test_a_configuration_without_roles_passes(checked_project):
    models = checked_project / "config" / "models.toml"
    models.write_text(models.read_text(encoding="utf-8").split("[roles.fast]")[0], encoding="utf-8")

    line = services.check_model_server(checked_project, answering({}))

    assert line == CheckLine(PASS, "model_server", "config/models.toml defines no model role")


def never_asked(settings, autostart):
    pytest.fail("no role may be asked")


@pytest.mark.parametrize(
    "listed, asked, line",
    [
        (
            '["strong"]',
            ["strong"],
            "autostart roles strong; strong answered a 1-token completion within 30 s; not autostarted: fast, small",
        ),
        (
            None,
            ["strong"],
            "autostart roles strong; strong answered a 1-token completion within 30 s; not autostarted: fast, small",
        ),
        (
            '["strong", "fast"]',
            ["fast", "strong"],
            "autostart roles fast, strong; fast, strong each answered a 1-token completion within 30 s; "
            "not autostarted: small",
        ),
    ],
    ids=["strong", "no list means strong", "two roles in the file's order"],
)
def test_with_autostart_only_the_listed_roles_are_asked_and_the_line_names_them(checked_project, listed, asked, line):
    set_autostart(checked_project, True, listed)
    probe = answering({"fast": None, "strong": None, "small": None})

    found = services.check_model_server(checked_project, probe)

    assert found == CheckLine(PASS, "model_server", line)
    assert probe.seen == [(asked, True)]


def test_a_listed_optional_role_that_does_not_answer_is_a_warning_naming_the_autostart_roles(checked_project):
    set_autostart(checked_project, True, '["strong"]')

    line = services.check_model_server(checked_project, answering({"strong": "no answer (ReadTimeout)"}))

    assert line == CheckLine(
        WARN,
        "model_server",
        "autostart roles strong; strong (optional) did not answer: no answer (ReadTimeout); not autostarted: fast, small",
    )


def test_a_required_role_left_out_of_autostart_fails_since_the_agent_never_serves_it(checked_project):
    set_autostart(checked_project, True, '["strong"]')
    require(checked_project, "fast")

    line = services.check_model_server(checked_project, answering({"strong": None}))

    assert line == CheckLine(
        FAIL,
        "model_server",
        f"autostart roles strong; fast (required) did not answer: {services.NOT_AUTOSTARTED}; not autostarted: small",
    )


def test_an_autostart_role_the_model_settings_do_not_have_fails_naming_it_and_nothing_is_asked(checked_project):
    set_autostart(checked_project, True, '["strong", "huge"]')

    line = services.check_model_server(checked_project, never_asked)

    assert line == CheckLine(FAIL, "model_server", "autostart roles strong, huge; config/models.toml has no role huge")


def test_an_empty_autostart_list_serves_no_role_and_each_is_said_not_to_answer(checked_project):
    set_autostart(checked_project, True, "[]")

    line = services.check_model_server(checked_project, never_asked)

    assert line.status == WARN and line.detail.startswith("autostart roles none; fast (optional) did not answer: ")
    assert line.detail.count(services.NO_AUTOSTART_ROLES) == 3


@pytest.fixture
def server_calls(monkeypatch) -> list[str]:
    """The included server's start and stop, replaced: start says something on the console, as the real one does."""
    calls = []

    def start(roles):
        calls.append(f"start {', '.join(roles)}")
        print_line("Model server: role fast is ready on port 41500.")

    monkeypatch.setattr(modelserver, "start", start)
    monkeypatch.setattr(modelserver, "stop", lambda: calls.append("stop"))
    return calls


def llama_roles(root) -> server_settings.ModelServerSettings:
    """The model server settings under `root` with its llama roles alone: the roles these probes ask a completion."""
    return dataclasses.replace(server_settings.load(root), systemone_roles=())


def endpoint(answers: dict[str, int | Exception]) -> httpx.MockTransport:
    """The endpoint's answer to a completion for each role: an HTTP status, or an error raised instead."""

    def answer(request: httpx.Request) -> httpx.Response:
        role = httpx.Response(200, content=request.content).json()["model"]
        if isinstance(answers[role], Exception):
            raise answers[role]
        return httpx.Response(answers[role], json={"choices": []})

    return httpx.MockTransport(answer)


def test_with_autostart_the_server_is_started_asked_through_its_endpoint_and_stopped(
    checked_project, server_calls, capsys
):
    settings = llama_roles(checked_project)
    transport = endpoint({"fast": 200, "strong": httpx.ConnectError("refused"), "small": 200})

    failures = services.probe_included_server(settings, True, transport=transport)

    assert failures == {"fast": None, "strong": "no answer (ConnectError)", "small": None}
    assert server_calls == ["start fast, strong, small", "stop"] and capsys.readouterr().out == ""


def test_the_server_is_started_with_only_the_roles_it_is_given_and_only_they_are_asked(checked_project, server_calls):
    settings = llama_roles(checked_project)
    strong_only = dataclasses.replace(settings, roles=tuple(role for role in settings.roles if role.name == "strong"))

    failures = services.probe_included_server(strong_only, True, transport=endpoint({"strong": 200}))

    assert failures == {"strong": None} and server_calls == ["start strong", "stop"]


def test_a_server_already_on_the_endpoint_is_asked_and_left_running(checked_project, monkeypatch):
    def port_taken(roles):
        raise OSError("port 11500 is in use")

    monkeypatch.setattr(modelserver, "start", port_taken)
    monkeypatch.setattr(modelserver, "stop", lambda: pytest.fail("a server the check did not start is never stopped"))

    failures = services.probe_included_server(
        llama_roles(checked_project), True, transport=endpoint({"fast": 503, "strong": 200, "small": 200})
    )

    assert failures == {"fast": "answered HTTP 503", "strong": None, "small": None}


def test_an_answer_after_the_limit_does_not_count(checked_project, server_calls, monkeypatch):
    monkeypatch.setattr(services, "COMPLETION_LIMIT_SECONDS", 0.0)

    failures = services.probe_included_server(
        llama_roles(checked_project), True, transport=endpoint({"fast": 200, "strong": 200, "small": 200})
    )

    assert all(reason.startswith("answered after") for reason in failures.values())


def test_without_autostart_nothing_is_started(checked_project, server_calls):
    failures = services.probe_included_server(llama_roles(checked_project), False)

    assert failures == dict.fromkeys(["fast", "strong", "small"], services.AUTOSTART_OFF) and server_calls == []


# -- jev --------------------------------------------------------------------------------------------


class StubSource(DecisionSource):
    """A decision source whose one call ends as a test says."""

    name = "jev"

    def __init__(self, calls: list[SourceCall]) -> None:
        self._calls = calls
        self.asked = []

    def ask(self, request):
        self.asked.append(request)
        return self._calls


def enable_jev(root) -> None:
    agent = root / "config" / "agent.toml"
    agent.write_text(agent.read_text(encoding="utf-8").replace("enabled = false", "enabled = true"), encoding="utf-8")


def test_jev_disabled_is_skipped(checked_project):
    line = services.check_jev(checked_project, lambda settings: pytest.fail("a disabled Jev is never asked"))

    assert line == CheckLine(SKIP, "jev", "disabled")


def test_jev_answering_one_of_two_options_passes(checked_project):
    enable_jev(checked_project)
    source = StubSource([SourceCall("jev", "B", 0.9, 240, USED)])

    line = services.check_jev(checked_project, lambda settings: source)

    (request,) = source.asked
    assert line == CheckLine(PASS, "jev", "answered B of 2 options in 240 ms")
    assert request.option_ids() == ["A", "B"] and request.deadline_ms == 5000


@pytest.mark.parametrize(
    "calls, said",
    [
        ([SourceCall("jev", None, None, 5000, TIMEOUT, "deadline")], "no option within 5 s (timeout, deadline)"),
        (
            [SourceCall("jev", "C", 0.9, 30, "invalid", "not_an_option")],
            "no option within 5 s (invalid, not_an_option)",
        ),
        ([], "no option within 5 s (no call was made)"),
    ],
    ids=["timeout", "not an option", "no call"],
)
def test_jev_without_an_option_in_time_is_a_warning(checked_project, calls, said):
    enable_jev(checked_project)

    line = services.check_jev(checked_project, lambda settings: StubSource(calls))

    assert line == CheckLine(WARN, "jev", f"{said}; the agent plays without Jev")


def test_a_jev_source_that_raises_is_a_warning(checked_project):
    enable_jev(checked_project)

    def broken(settings):
        raise RuntimeError("TLS handshake failed")

    line = services.check_jev(checked_project, broken)

    assert line.status == WARN and "RuntimeError: TLS handshake failed" in line.detail


def test_the_real_jev_source_without_a_key_sends_nothing_and_is_a_warning(checked_project, monkeypatch):
    enable_jev(checked_project)
    monkeypatch.delenv("JEV_API_KEY", raising=False)

    line = services.check_jev(checked_project)

    assert line == CheckLine(WARN, "jev", f"no option within 5 s ({ERROR}, no_api_key); the agent plays without Jev")


def test_jev_settings_that_cannot_be_read_are_a_warning(checked_project):
    (checked_project / "config" / "agent.toml").write_text("[models]\nautostart = false\n", encoding="utf-8")

    line = services.check_jev(checked_project)

    assert line.status == WARN and line.detail.startswith("the Jev settings cannot be read")
