"""`python -m ucla_agent.check`: eight lines in order, the games that are ready, and the exit code.

The platform, the model server and Jev are replaced by stubs; everything else
the check reads is a temporary project holding the tracked configuration.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from altruagent.errors import PlatformError
from ucla_agent.check import command, fallback, local
from ucla_agent.check.command import Services
from ucla_agent.check.line import FAIL, PASS, CheckLine
from ucla_agent.core import paths
from ucla_agent.core.decide import USED, DecisionSource, JevSource, LocalModelSource
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.core.modelserver import settings as server_settings

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
CHECK_NAMES = ["config", "secrets_scan", "platform", "games", "model_server", "jev", "records", "fallback"]
# Secret values of the test environment: shaped like an Official Agent Key, a Jev key and a token, and no real ones.
AGENT_KEY = "eak_live_" + "fedcba9876543210" * 4
JEV_KEY = "jev-test-key-7Hq2Lx9Pz"
SERVICE_TOKEN = "token-for-the-test-4Rk8Wm"


class HealthyClient:
    """The official client of a platform that answers everything."""

    def authenticate(self) -> None:
        pass

    def assignments(self) -> list:
        return []

    def close(self) -> None:
        pass


class AnsweringJev(DecisionSource):
    name = "jev"

    def ask(self, request):
        return [SourceCall("jev", request.option_ids()[0], 0.9, 120, USED)]


def every_role_answers(settings, autostart):
    return {role.name: None for role in settings.roles}


HEALTHY = Services(
    platform_client=HealthyClient, model_server_probe=every_role_answers, jev_source=lambda settings: AnsweringJev()
)


def broken(error: Exception):
    """A stand-in for a service that raises `error` whatever it is asked."""

    def fail(*_arguments, **_options):
        raise error

    return fail


@pytest.fixture
def project(checked_project, model_ports, monkeypatch) -> Path:
    """The temporary project with Jev enabled, so that all eight checks have something to check.

    Its model server's backend ports are moved to free test ports, so the check's look at the autostarted jev9b
    port never takes the live agent's.
    """
    monkeypatch.setenv(server_settings.BACKEND_PORT_BASE_ENV, str(model_ports.backend_port_base))
    agent_toml = checked_project / "config" / "agent.toml"
    agent_toml.write_text(agent_toml.read_text(encoding="utf-8").replace("enabled = false", "enabled = true"), "utf-8")
    return checked_project


def run_check(capsys, arguments=(), reach: Services = HEALTHY) -> tuple[int, list[str]]:
    exit_code = command.main(list(arguments), reach)
    return exit_code, capsys.readouterr().out.splitlines()


def line_of(lines: list[str], name: str) -> str:
    (line,) = [line for line in lines if line.split(" ")[1:2] == [f"{name}:"]]
    return line


def test_1_all_healthy_gives_eight_lines_in_order_without_a_failure_and_every_registered_game_ready(project, capsys):
    exit_code, lines = run_check(capsys)

    assert exit_code == 0 and len(lines) == 9
    assert [line.split(" ")[1].rstrip(":") for line in lines[:8]] == CHECK_NAMES
    statuses = [line.split(" ")[0] for line in lines[:8]]
    assert "FAIL" not in statuses and statuses == ["PASS"] * 8
    assert lines[8] == "Games ready: red_alert, werewolf, pokemon_vgc_doubles_draft"


def test_the_model_server_line_names_the_autostart_roles_the_shipped_configuration_starts(project, capsys):
    asked = []

    def probe(settings, autostart):
        asked.append(([role.name for role in settings.roles], autostart))
        return every_role_answers(settings, autostart)

    _, lines = run_check(capsys, reach=Services(HealthyClient, probe, HEALTHY.jev_source))

    assert line_of(lines, "model_server") == (
        "PASS model_server: autostart roles strong, jev9b; strong answered a 1-token completion within 30 s; "
        "jev9b answered /healthz within 30 s; not autostarted: fast, small"
    )
    assert asked == [(["strong"], True)]


def test_2_a_required_model_role_that_does_not_answer_fails_the_check(project, capsys):
    models = project / "config" / "models.toml"
    head, strong = models.read_text(encoding="utf-8").split("[roles.strong]")
    models.write_text(head + "[roles.strong]" + strong.replace("required = false", "required = true", 1), "utf-8")
    reach = Services(HealthyClient, lambda settings, autostart: {"strong": "no answer (ReadTimeout)"})

    exit_code, lines = run_check(capsys, reach=reach)

    assert line_of(lines, "model_server").startswith("FAIL model_server") and exit_code == 1


def test_the_optional_strong_role_not_answering_is_a_warning_and_the_check_still_passes(project, capsys):
    reach = Services(HealthyClient, lambda settings, autostart: {"strong": "no answer (ReadTimeout)"})

    exit_code, lines = run_check(capsys, reach=reach)

    assert line_of(lines, "model_server") == (
        "WARN model_server: autostart roles strong, jev9b; strong (optional) did not answer: no answer (ReadTimeout); "
        "not autostarted: fast, small"
    )
    assert exit_code == 0


def test_the_optional_jev9b_role_not_answering_is_a_warning_naming_it_and_the_check_still_passes(project, capsys):
    reach = Services(HealthyClient, lambda settings, autostart: {"jev9b": "no answer to /healthz (ConnectError)"})

    exit_code, lines = run_check(capsys, reach=reach)

    assert line_of(lines, "model_server") == (
        "WARN model_server: autostart roles strong, jev9b; jev9b (optional) did not answer: no answer to /healthz "
        "(ConnectError); not autostarted: fast, small"
    )
    assert exit_code == 0


def test_3_a_failing_jev_is_a_warning_and_the_check_still_passes(project, capsys):
    reach = Services(HealthyClient, every_role_answers, broken(RuntimeError("Jev is down")))

    exit_code, lines = run_check(capsys, reach=reach)

    assert line_of(lines, "jev").startswith("WARN jev") and exit_code == 0


def test_4_without_an_agent_key_the_platform_is_a_warning_and_the_check_still_passes(project, capsys, monkeypatch):
    monkeypatch.delenv("ALTRUAGENT_OFFICIAL_AGENT_KEY")

    exit_code, lines = run_check(capsys)

    assert line_of(lines, "platform") == "WARN platform: no key set" and exit_code == 0


def test_5_offline_skips_platform_model_server_and_jev_without_reaching_them(project, capsys):
    nothing = broken(AssertionError("an offline check reached a service"))

    exit_code, lines = run_check(capsys, ["--offline"], Services(nothing, nothing, nothing))

    assert [line_of(lines, name) for name in ("platform", "model_server", "jev")] == [
        "SKIP platform: skipped (--offline)",
        "SKIP model_server: skipped (--offline)",
        "SKIP jev: skipped (--offline)",
    ]
    assert exit_code == 0 and lines[8] == "Games ready: red_alert, werewolf, pokemon_vgc_doubles_draft"


def test_6_no_secret_of_the_environment_is_printed_even_when_a_service_repeats_it(project, capsys, monkeypatch):
    for name, value in (("ALTRUAGENT_OFFICIAL_AGENT_KEY", AGENT_KEY), ("JEV_API_KEY", JEV_KEY)):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)
    reach = Services(
        broken(PlatformError(f"the key {AGENT_KEY} was refused", status_code=None)),
        broken(RuntimeError(f"the server echoed {SERVICE_TOKEN}")),
        broken(RuntimeError(f"Jev echoed {JEV_KEY}")),
    )

    _, lines = run_check(capsys, reach=reach)
    output = "\n".join(lines)

    assert all(secret not in output for secret in (AGENT_KEY, JEV_KEY, SERVICE_TOKEN))
    assert output.count("[REDACTED]") >= 3


def test_7_the_fallback_passes_when_every_decision_source_raises(project, capsys, monkeypatch):
    monkeypatch.setattr(LocalModelSource, "ask", broken(ConnectionError("no model")))
    monkeypatch.setattr(JevSource, "ask", broken(ConnectionError("no Jev")))
    reach = Services(HealthyClient, broken(ConnectionError("no model server")), broken(ConnectionError("no Jev")))

    _, lines = run_check(capsys, reach=reach)

    assert line_of(lines, "fallback").startswith("PASS fallback: with every decision source failing")


def test_a_check_that_breaks_unexpectedly_is_reported_on_its_line_and_the_others_still_run(
    project, capsys, monkeypatch
):
    monkeypatch.setattr(local, "check_secrets_scan", broken(OSError("python could not be started")))

    exit_code, lines = run_check(capsys)

    assert (
        line_of(lines, "secrets_scan")
        == "FAIL secrets_scan: the check could not run: OSError: python could not be started"
    )
    assert len(lines) == 9 and exit_code == 1


def test_a_jev_check_that_breaks_unexpectedly_is_only_a_warning(project, capsys, monkeypatch):
    monkeypatch.setattr(command.services, "check_jev", broken(KeyError("fields")))

    exit_code, lines = run_check(capsys)

    assert line_of(lines, "jev").startswith("WARN jev: the check could not run: KeyError") and exit_code == 0


def test_red_alert_is_not_ready_when_its_fallback_fails(project, capsys, monkeypatch):
    monkeypatch.setattr(fallback, "check_fallback", lambda root: CheckLine(FAIL, "fallback", "answered WAIT"))

    exit_code, lines = run_check(capsys)

    assert lines[8] == "Games ready: werewolf, pokemon_vgc_doubles_draft" and exit_code == 1


def test_no_game_is_ready_when_the_games_check_breaks(project, capsys, monkeypatch):
    monkeypatch.setattr(local, "check_games", broken(ImportError("werewolf")))

    _, lines = run_check(capsys)

    assert line_of(lines, "games").startswith("FAIL games: the check could not run") and lines[8] == "Games ready: none"


def test_the_command_runs_as_a_module_and_reads_the_project_it_is_pointed_at(project):
    environment = {**os.environ, "PYTHONPATH": str(SOURCE_ROOT), paths.ROOT_ENV: str(project)}

    completed = subprocess.run(
        [sys.executable, "-m", "ucla_agent.check", "--offline"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
    )

    lines = completed.stdout.splitlines()
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert [line.split(" ")[0] for line in lines[:8]] == ["PASS", "PASS", "SKIP", "PASS", "SKIP", "SKIP", "PASS", PASS]
    assert (
        lines[8] == "Games ready: red_alert, werewolf, pokemon_vgc_doubles_draft"
        and (project / "runs" / ".check" / "probe.jsonl").is_file()
    )
