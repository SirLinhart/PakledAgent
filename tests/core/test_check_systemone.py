"""The readiness check's model_server line with the shipped systemone role jev9b: asked for its `/healthz` when it is
autostarted, left out of the line when it is not, and a port already in use noted without failing."""

from __future__ import annotations

import socket

import httpx
import pytest

from ucla_agent.check import services
from ucla_agent.check.line import PASS, WARN, CheckLine
from ucla_agent.core import modelserver
from ucla_agent.core.modelserver import settings as server_settings


def answering(failures: dict[str, str | None]):
    """A role probe answering for every role as given; it remembers the llama and systemone roles it was given."""
    seen = []

    def probe(settings, autostart):
        seen.append(([role.name for role in settings.roles], [role.name for role in settings.systemone_roles]))
        return failures

    probe.seen = seen
    return probe


def set_autostart_roles(root, roles: str) -> None:
    """Set autostart on and autostart_roles in the copied `config/agent.toml`."""
    agent = root / "config" / "agent.toml"
    lines = [
        line
        for line in agent.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("autostart =", "autostart_roles ="))
    ]
    models = lines.index("[models]") + 1
    lines[models:models] = ["autostart = true", f"autostart_roles = {roles}"]
    agent.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def project(checked_project, model_ports, monkeypatch):
    """The checked project with its model server's backend ports moved to free test ports: jev9b is on the fourth."""
    monkeypatch.setenv(server_settings.BACKEND_PORT_BASE_ENV, str(model_ports.backend_port_base))
    return checked_project


def test_an_autostarted_jev9b_is_asked_and_named_beside_strong(project):
    set_autostart_roles(project, '["strong", "jev9b"]')
    probe = answering({"strong": None, "jev9b": None})

    line = services.check_model_server(project, probe)

    assert probe.seen == [(["strong"], ["jev9b"])]
    assert line == CheckLine(
        PASS,
        "model_server",
        "autostart roles strong, jev9b; strong answered a 1-token completion within 30 s; "
        "jev9b answered /healthz within 30 s; not autostarted: fast, small",
    )


def test_with_the_4b_selected_jev9b_is_neither_asked_nor_named(project):
    set_autostart_roles(project, '["strong", "small"]')
    probe = answering({"strong": None, "small": None})

    line = services.check_model_server(project, probe)

    assert probe.seen == [(["strong", "small"], [])]
    assert "jev9b" not in line.detail and line.status == PASS


def test_an_autostarted_jev9b_that_does_not_answer_is_a_warning(project):
    set_autostart_roles(project, '["jev9b"]')

    line = services.check_model_server(project, answering({"jev9b": "no answer to /healthz (ConnectError)"}))

    assert line == CheckLine(
        WARN,
        "model_server",
        "autostart roles jev9b; jev9b (optional) did not answer: no answer to /healthz (ConnectError); "
        "not autostarted: fast, strong, small",
    )


def test_a_jev9b_port_already_in_use_is_noted_and_does_not_fail_the_line(project, model_ports):
    set_autostart_roles(project, '["jev9b"]')
    port = model_ports.backend_port(3)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", port))
        taken.listen()

        line = services.check_model_server(project, answering({"jev9b": None}))

    assert line.status == PASS
    assert line.detail.endswith(
        f"; note: port {port} of jev9b was in use before the check, so the server already there was asked"
    )


@pytest.mark.parametrize(
    "answer, failure",
    [
        (httpx.Response(200, json={"status": "ok"}), None),
        (httpx.Response(503, json={"status": "loading"}), "answered /healthz with HTTP 503"),
        (httpx.ConnectError("refused"), "no answer to /healthz (ConnectError)"),
    ],
    ids=["ok", "503", "refused"],
)
def test_the_probe_asks_a_systemone_role_its_healthz_on_its_own_port(
    project, model_ports, monkeypatch, answer, failure
):
    calls = []
    monkeypatch.setattr(modelserver, "start", lambda roles: calls.append(f"start {', '.join(roles)}"))
    monkeypatch.setattr(modelserver, "stop", lambda: calls.append("stop"))
    asked = []

    def respond(request: httpx.Request) -> httpx.Response:
        asked.append((request.method, str(request.url)))
        if isinstance(answer, Exception):
            raise answer
        return answer

    loaded = server_settings.load(project)
    jev9b_only = server_settings.ModelServerSettings(
        loaded.host, loaded.port, loaded.llama_server_path, loaded.backend_port_base, False, (), loaded.systemone_roles
    )

    failures = services.probe_included_server(jev9b_only, True, transport=httpx.MockTransport(respond))

    assert failures == {"jev9b": failure}
    assert asked == [("GET", f"http://127.0.0.1:{model_ports.backend_port(3)}/healthz")]
    assert calls == ["start jev9b", "stop"]
