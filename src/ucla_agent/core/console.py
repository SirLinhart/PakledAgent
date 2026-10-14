"""The agent's console output.

Everything the agent prints goes through `print_line`, which redacts secrets,
so a key that ends up in a message (an error text, a URL, a path) never
reaches a terminal, a log file or a screenshot. This is the one module that
touches the standard streams.
"""

from __future__ import annotations

import sys

from ucla_agent.core.secrets import redact


def print_line(text: str, *, error: bool = False) -> None:
    """Print one line with secrets redacted: to standard output, or to standard error when `error` is true."""
    print(redact(text), file=sys.stderr if error else sys.stdout, flush=True)


def can_print(text: str) -> bool:
    """Whether the console's encoding has every character of `text` (a redirected Windows console may lack some)."""
    try:
        text.encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True
