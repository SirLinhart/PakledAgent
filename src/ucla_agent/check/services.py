"""The readiness checks that reach a service: the tournament platform, the included model server and Jev.

Each check reaches its service through a function it is given, whose default
is the real thing, so that the checks can be run against stubs. None of them
plays a move or changes anything on the platform: the platform check signs in
and lists the agent's assignments, which is all `python -m agent
--check-tournament` does too.

What a failure costs decides its status. The platform and a model role the
configuration requires are needed to join and finish a match, so their
failure is FAIL. A model role that is not required and Jev are optional: the
agent plays on its scripted choices without them, so their failure is WARN,
whatever went wrong.

A systemone role is asked for its `/healthz` on its own port instead of a
completion: it answers no chat completion, and its server listens only once
its model is loaded.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import time
from pathlib import Path
from typing import Callable, Collection, Iterator, Protocol

import httpx

from altruagent.errors import AltruAgentError, ConfigurationError
from altruagent.official import OFFICIAL_AGENT_KEY_ENV, OfficialAgentClient, OfficialAgentError, is_fatal_auth_error
from ucla_agent.check.line import FAIL, PASS, SKIP, WARN, CheckLine
from ucla_agent.core import modelserver
from ucla_agent.core.config import ConfigError, autostart_roles, load_agent_config
from ucla_agent.core.decide import ChoiceRequest, DecisionSource, JevSource, Option
from ucla_agent.core.decide import settings as decide_settings
from ucla_agent.core.decide.settings import JevSettings
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.modelserver.roles import CLIENT_HEADER, CLIENT_NAME, COMPLETIONS_ROUTE, HTTP_OK, port_is_free
from ucla_agent.core.modelserver.settings import ModelServerSettings, RoleSettings, SystemoneRoleSettings
from ucla_agent.core.modelserver.systemone import HEALTHZ_ROUTE
from ucla_agent.core.secrets import BoundClient

PLATFORM = "platform"
MODEL_SERVER = "model_server"
JEV = "jev"
NO_KEY_SET = "no key set"
# The answers by which the platform refuses the key itself, as `python -m agent --check-tournament` reads them.
KEY_REFUSED_STATUSES = (400, 401)
COMPLETION_LIMIT_SECONDS = 30.0
ONE_TOKEN_COMPLETION = {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 1}
AUTOSTART_OFF = "the agent does not start the model server ([models].autostart is false in config/agent.toml)"
NO_AUTOSTART_ROLES = "the agent starts no model role ([models].autostart_roles is empty in config/agent.toml)"
NOT_AUTOSTARTED = "the agent does not start it (it is not in [models].autostart_roles of config/agent.toml)"
HEALTHZ_LIMIT_SECONDS = 30.0
PORT_TAKEN_NOTE = "note: port {port} of {role} was in use before the check, so the server already there was asked"
JEV_DEADLINE_MS = 5000
JEV_REQUEST = ChoiceRequest(
    question="Which option comes first?",
    options=(Option("A", "the first option"), Option("B", "the second option")),
    context="A readiness check of the agent; nothing depends on the answer.",
    deadline_ms=JEV_DEADLINE_MS,
    state_tag="readiness-check",
)
_PLAYS_WITHOUT_JEV = "the agent plays without Jev"


class PlatformClient(Protocol):
    """What the platform check needs of the official client."""

    def authenticate(self) -> None:
        """Sign in with the Official Agent Key."""

    def assignments(self) -> list:
        """The agent's active assignments."""

    def close(self) -> None:
        """Close the connections."""


def official_client() -> OfficialAgentClient:
    """The vendored runtime's client for the key in the environment; the command has loaded `.env` already."""
    return OfficialAgentClient(load_env_file=False)


def check_platform(new_client: Callable[[], PlatformClient] = official_client) -> CheckLine:
    """The control plane is reachable, the key is accepted and the assignments can be listed; WARN without a key."""
    if not os.environ.get(OFFICIAL_AGENT_KEY_ENV, "").strip():
        return CheckLine(WARN, PLATFORM, NO_KEY_SET)
    try:
        client = new_client()
    except ConfigurationError as exc:
        return CheckLine(FAIL, PLATFORM, f"the key cannot be used: {exc}")
    try:
        return _sign_in_and_list(client)
    finally:
        client.close()


def _sign_in_and_list(client: PlatformClient) -> CheckLine:
    try:
        client.authenticate()
    except OfficialAgentError as exc:
        refused = exc.status_code in KEY_REFUSED_STATUSES and is_fatal_auth_error(exc)
        what = "Official Agent Key rejected" if refused else "authentication failed"
        return CheckLine(FAIL, PLATFORM, f"control plane reachable, {what}: {exc}")
    except AltruAgentError as exc:
        return CheckLine(FAIL, PLATFORM, f"control plane not reachable: {exc}")
    try:
        assignments = client.assignments()
    except AltruAgentError as exc:
        return CheckLine(
            FAIL, PLATFORM, f"control plane reachable and key accepted, assignment discovery failed: {exc}"
        )
    found = f"{len(assignments)} active assignment(s)"
    return CheckLine(PASS, PLATFORM, f"control plane reachable, key accepted, assignment discovery works ({found})")


# -- the model server -------------------------------------------------------------------------------

# Given the model server's settings, holding only the roles to ask, and whether the agent starts the server: each of
# those roles -> None when it answered, else why not.
RoleProbe = Callable[[ModelServerSettings, bool], dict[str, str | None]]


def probe_included_server(
    settings: ModelServerSettings, autostart: bool, *, transport: httpx.BaseTransport | None = None
) -> dict[str, str | None]:
    """Ask every role of `settings` a 1-token completion through the included server's endpoint, as the agent runs it,
    and every systemone role of it for its `/healthz` on that role's own port.

    The server is started here with only those roles and stopped again,
    unless its endpoint's port is taken: then a server is already serving
    there, most likely the agent's own, and that one is asked. With autostart
    off no server runs during play, so no role is asked and none answers.
    Raises `RequiredModelUnavailable` when a required role cannot be brought
    up. `transport` replaces the network in tests.
    """
    if not autostart:
        return {role.name: AUTOSTART_OFF for role in settings.every_role}
    with _included_server_running([role.name for role in settings.every_role]):
        client = BoundClient(settings.endpoint_url, headers={CLIENT_HEADER: CLIENT_NAME}, transport=transport)
        try:
            failures = {role.name: _completion_failure(client, role.name) for role in settings.roles}
        finally:
            client.close()
        native = {
            role.name: _healthz_failure(settings.systemone_url(role), transport) for role in settings.systemone_roles
        }
    return failures | native


@contextlib.contextmanager
def _included_server_running(roles: Collection[str]) -> Iterator[None]:
    try:
        # The server says how each role came up; the check has one line for all of it.
        with contextlib.redirect_stdout(io.StringIO()):
            modelserver.start(roles=roles)
    except OSError:
        # The endpoint's port is taken: a server already listens there and is asked as it is.
        yield
        return
    try:
        yield
    finally:
        modelserver.stop()


def _completion_failure(client: BoundClient, role: str) -> str | None:
    """None when the role answers a 1-token completion within the limit; else what happened instead."""
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            COMPLETIONS_ROUTE,
            json_body={"model": role, **ONE_TOKEN_COMPLETION},
            timeout=COMPLETION_LIMIT_SECONDS,
        )
    except httpx.HTTPError as exc:
        return f"no answer ({type(exc).__name__})"
    seconds = time.perf_counter() - started
    if response.status_code != HTTP_OK:
        return f"answered HTTP {response.status_code}"
    if seconds > COMPLETION_LIMIT_SECONDS:
        return f"answered after {seconds:.1f} s"
    return None


def _healthz_failure(base_url: str, transport: httpx.BaseTransport | None) -> str | None:
    """None when a systemone server answers its `/healthz` with 200 within the limit; else what happened instead."""
    client = BoundClient(base_url, headers={CLIENT_HEADER: CLIENT_NAME}, transport=transport)
    try:
        response = client.request("GET", HEALTHZ_ROUTE, timeout=HEALTHZ_LIMIT_SECONDS)
    except httpx.HTTPError as exc:
        return f"no answer to {HEALTHZ_ROUTE} ({type(exc).__name__})"
    finally:
        client.close()
    if response.status_code != HTTP_OK:
        return f"answered {HEALTHZ_ROUTE} with HTTP {response.status_code}"
    return None


def check_model_server(root: Path, probe: RoleProbe = probe_included_server) -> CheckLine:
    """Each role the agent starts answers a 1-token completion within 30 s, or the line is WARN (FAIL if required).

    With autostart on the line begins with the roles `[models] autostart_roles`
    lists, and only they are started and asked. A role left out of the list is
    named as not autostarted, unless it is required: the agent never serves
    it, so a required one fails. With autostart off, or an empty list, no role
    is served in play and each is said not to answer. A systemone role is one
    engine the Red Alert chains may be swapped to, so it is judged only when
    it is listed or required, and asked for its `/healthz`.
    """
    try:
        settings = server_settings.load(root)
        agent_config = load_agent_config(root)
    except ConfigError as exc:
        return CheckLine(FAIL, MODEL_SERVER, f"the model server's settings cannot be read: {exc}")
    if not settings.every_role:
        return CheckLine(PASS, MODEL_SERVER, "config/models.toml defines no model role")
    if not agent_config["models"]["autostart"]:
        return _probed(settings, _judged_roles(settings, ()), probe, autostart=False)
    return _autostarted(settings, autostart_roles(agent_config), probe)


def _judged_roles(
    settings: ModelServerSettings, listed: tuple[str, ...]
) -> tuple[RoleSettings | SystemoneRoleSettings, ...]:
    """The roles the line judges: every llama role, and each systemone role that is listed or required."""
    native = (role for role in settings.systemone_roles if role.name in listed or role.required)
    return (*settings.roles, *native)


def _autostarted(settings: ModelServerSettings, listed: tuple[str, ...], probe: RoleProbe) -> CheckLine:
    """The line with autostart on: the listed roles, how each answered, and the roles left out."""
    judged = _judged_roles(settings, listed)
    unknown = sorted(set(listed) - {role.name for role in settings.every_role})
    if unknown:
        return CheckLine(
            FAIL,
            MODEL_SERVER,
            f"autostart roles {', '.join(listed)}; config/models.toml has no role {', '.join(unknown)}",
        )
    served = dataclasses.replace(
        settings,
        roles=tuple(role for role in settings.roles if role.name in listed),
        systemone_roles=tuple(role for role in settings.systemone_roles if role.name in listed),
    )
    # Taken before the check starts anything: afterwards the check's own server would hold the port.
    notes = [
        PORT_TAKEN_NOTE.format(port=served.systemone_port(role), role=role.name)
        for role in served.systemone_roles
        if not port_is_free(served.systemone_port(role))
    ]
    if served.every_role:
        line = _probed(served, judged, probe, autostart=True)
    else:
        line = _judged(judged, {role.name: NO_AUTOSTART_ROLES for role in judged})
    names = ", ".join(role.name for role in served.every_role) or "none"
    return dataclasses.replace(line, detail="; ".join([f"autostart roles {names}", line.detail, *notes]))


def _probed(
    served: ModelServerSettings,
    roles: tuple[RoleSettings | SystemoneRoleSettings, ...],
    probe: RoleProbe,
    *,
    autostart: bool,
) -> CheckLine:
    """Judge `roles` by how the served ones answered the probe; a role that is not served is not autostarted."""
    try:
        failures = probe(served, autostart)
    except modelserver.RequiredModelUnavailable as exc:
        return CheckLine(FAIL, MODEL_SERVER, str(exc))
    except Exception as exc:  # noqa: BLE001 - what the server's failure costs is judged by the roles below
        failures = {role.name: f"{type(exc).__name__}: {exc}" for role in served.every_role}
    left_out = {role.name: NOT_AUTOSTARTED for role in roles if role not in served.every_role}
    return _judged(roles, failures | left_out)


def _judged(roles: tuple[RoleSettings | SystemoneRoleSettings, ...], failures: dict[str, str | None]) -> CheckLine:
    """PASS when every judged role answered, else FAIL or WARN; an optional role left out of autostart is only named."""
    left_out = [role.name for role in roles if failures.get(role.name) == NOT_AUTOSTARTED and not role.required]
    judged = [role for role in roles if role.name not in left_out]
    down = [(role, failures[role.name]) for role in judged if failures.get(role.name)]
    line = _did_not_answer(down) if down else _all_answered(judged)
    if left_out:
        return dataclasses.replace(line, detail=f"{line.detail}; not autostarted: {', '.join(left_out)}")
    return line


def _all_answered(roles: list[RoleSettings | SystemoneRoleSettings]) -> CheckLine:
    chat = [role.name for role in roles if not isinstance(role, SystemoneRoleSettings)]
    native = [role.name for role in roles if isinstance(role, SystemoneRoleSettings)]
    parts = [_answered(chat, "a 1-token completion")] if chat or not native else []
    if native:
        parts.append(_answered(native, HEALTHZ_ROUTE))
    return CheckLine(PASS, MODEL_SERVER, "; ".join(parts))


def _answered(names: list[str], what: str) -> str:
    each = "each " if len(names) > 1 else ""
    return f"{', '.join(names)} {each}answered {what} within 30 s"


def _did_not_answer(down: list[tuple[RoleSettings | SystemoneRoleSettings, str]]) -> CheckLine:
    status = FAIL if any(role.required for role, _ in down) else WARN
    detail = "; ".join(
        f"{role.name} ({'required' if role.required else 'optional'}) did not answer: {reason}" for role, reason in down
    )
    return CheckLine(status, MODEL_SERVER, detail)


# -- Jev --------------------------------------------------------------------------------------------


def jev_source(settings: JevSettings) -> DecisionSource:
    """The Jev source the agent's chains use, with the key from the environment."""
    return JevSource(settings.base_url, min_confidence=settings.min_confidence, fields=settings.fields)


def check_jev(root: Path, new_source: Callable[[JevSettings], DecisionSource] = jev_source) -> CheckLine:
    """SKIP while Jev is disabled; else one decide call with two options answers one of them within 5 s, or WARN."""
    try:
        jev = decide_settings.load(root).jev
    except ConfigError as exc:
        return CheckLine(WARN, JEV, f"the Jev settings cannot be read ({exc}); {_PLAYS_WITHOUT_JEV}")
    if not jev.enabled:
        return CheckLine(SKIP, JEV, "disabled")
    try:
        calls = new_source(jev).ask(JEV_REQUEST)
    except Exception as exc:  # noqa: BLE001 - Jev is optional: whatever happens to it is a warning
        return CheckLine(WARN, JEV, f"the decide call failed ({type(exc).__name__}: {exc}); {_PLAYS_WITHOUT_JEV}")
    last = calls[-1] if calls else None
    if last is not None and last.answer in JEV_REQUEST.option_ids():
        return CheckLine(PASS, JEV, f"answered {last.answer} of 2 options in {last.latency_ms} ms")
    what = "no call was made" if last is None else f"{last.outcome}, {last.reason}"
    return CheckLine(WARN, JEV, f"no option within 5 s ({what}); {_PLAYS_WITHOUT_JEV}")
