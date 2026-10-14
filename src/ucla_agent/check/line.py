"""One line of the readiness check: which check, how it went, and what was found, in plain words."""

from __future__ import annotations

from dataclasses import dataclass

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class CheckLine:
    """The outcome of one check: `status` is PASS, WARN, FAIL or SKIP, `detail` says why in plain words."""

    status: str
    name: str
    detail: str

    def text(self) -> str:
        """The line as the check prints it: `<STATUS> <name>: <detail>`."""
        return f"{self.status} {self.name}: {self.detail}"
