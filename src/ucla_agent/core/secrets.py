"""Secrets: where they come from, and keeping them out of everything the agent writes or sends.

Secrets are read only from environment variables, which `load_secrets` fills
from the untracked `.env` in the repository root. `redact` removes them from
text. Every channel that leaves the process (a match record, a metrics file,
the console, a prompt) applies it once, at the one function that writes or
sends for that channel, so no caller has to remember to. `BoundClient` is that
function for outgoing requests, and it also keeps a service's credential from
reaching any host but that service's own.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

import httpx
from dotenv import load_dotenv

from ucla_agent.core.paths import repository_root

ENV_FILE_NAME = ".env"
REDACTED = "[REDACTED]"
# A shorter value would also match ordinary words and numbers all over a record.
MIN_SECRET_LENGTH = 8
SECRET_ENV_NAMES = frozenset({"ALTRUAGENT_OFFICIAL_AGENT_KEY", "ALTRUAGENT_CLAIM_TOKEN", "JEV_API_KEY"})
SECRET_ENV_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")
# Strings that are secrets by their shape alone, whether or not their value was
# loaded: an Official Agent Key, a seat claim code, an agent API key and a JWT.
SECRET_PATTERNS = (
    r"eak_live_[A-Za-z0-9_-]+",
    r"seatclaim_[A-Za-z0-9_-]+",
    r"sk_agent_[A-Za-z0-9_-]+",
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
)

_redaction: re.Pattern[str] | None = None


def is_secret_name(name: str) -> bool:
    """Whether an environment variable of this name holds a secret."""
    upper_name = name.upper()
    return upper_name in SECRET_ENV_NAMES or upper_name.endswith(SECRET_ENV_SUFFIXES)


def secret_values(variables: Mapping[str, str | None]) -> list[str]:
    """The values of the secret-named variables that are long enough to redact."""
    return sorted(
        {
            value
            for name, value in variables.items()
            if is_secret_name(name) and value and len(value) >= MIN_SECRET_LENGTH
        }
    )


def load_secrets() -> None:
    """Load `.env` from the repository root into the environment and remember every secret value for `redact`.

    A variable that is already set keeps its value. Call it again after the
    environment has changed.
    """
    global _redaction
    load_dotenv(repository_root() / ENV_FILE_NAME)
    _redaction = _build_redaction(secret_values(os.environ))


def redact(text: str) -> str:
    """`text` with every loaded secret value and every secret-shaped string replaced by `[REDACTED]`."""
    if _redaction is None:
        load_secrets()
    return _redaction.sub(REDACTED, text)


def _build_redaction(values: list[str]) -> re.Pattern[str]:
    """One pattern matching each of `values`, as written and as JSON text holds it, and every secret shape."""
    # Record lines and request bodies are JSON text, where a value holding a
    # quote, a backslash or a non-ASCII character appears in its escaped form.
    forms = set(values) | {json.dumps(value)[1:-1] for value in values}
    # Longest first, and before the shapes: when one value starts another, or a
    # value starts like a shape and goes on, the whole value is replaced.
    literals = [re.escape(form) for form in sorted(forms, key=lambda form: (-len(form), form))]
    return re.compile("|".join([*literals, *SECRET_PATTERNS]))


class SecretDestinationError(Exception):
    """A request was aimed at a host other than the one its client, and that client's credential, belong to."""


class BoundClient:
    """HTTP client for one service: it reaches only the base URL it was built for, and redacts what it sends.

    `headers` are sent with every request and are where the service's
    credential goes. A request for any other scheme, host or port is refused
    before anything is sent, and redirects are not followed, so the credential
    cannot leave for another host.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = httpx.URL(base_url)
        # trust_env off: a proxy named in the environment would be a second
        # host that sees the credential.
        self._http = httpx.Client(headers=headers, transport=transport, follow_redirects=False, trust_env=False)

    def request(self, method: str, url: str, *, json_body: Any = None, timeout: float) -> httpx.Response:
        """Send one request and return the service's answer, whatever its status.

        `url` is a path under the base URL, or an absolute URL of the same
        service. `json_body` is sent as JSON text with secrets redacted.
        `timeout` is in seconds. Raises `SecretDestinationError` for any other
        destination; `httpx` errors (no connection, timeout) reach the caller.
        """
        target = self._base_url.join(url)
        if _origin(target) != _origin(self._base_url):
            raise SecretDestinationError(
                f"A client bound to {self._base_url.host} was asked to call {target.host}; the request was not sent."
            )
        if json_body is None:
            return self._http.request(method, target, timeout=timeout)
        body = redact(json.dumps(json_body))
        return self._http.request(
            method, target, content=body, headers={"Content-Type": "application/json"}, timeout=timeout
        )

    def close(self) -> None:
        """Close the connections this client holds."""
        self._http.close()


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port
