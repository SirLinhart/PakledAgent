"""The Python side of the battle helper: one battle in a child process, spoken to in JSON lines.

The helper is `node tools/pokemon_sim/sim.js` (Showdown's battle stream), or
any other command that speaks its protocol, such as the fake helper the
default tests use. Each command is one JSON line on the helper's stdin, and
the helper answers each with exactly one JSON line on its stdout:

- `{"command": "start", "format": ..., "seed": ..., "players": {"p1": {"name": ..., "team": [...]}, "p2": ...}}`
- `{"command": "choose", "side": "p1", "choice": "move 1 2, switch 3"}`

An answer carries, per side, the log lines that side may see since the
previous answer, its latest request and the text of an error the simulator
sent it, plus whether the battle ended and the winner's name. A helper that
answers `{"ok": false}`, dies, or takes longer than the timeout raises
`BattleHelperError` with what it wrote on stderr.

The answers are read on a thread of their own, so a helper that stops
answering cannot hang a practice match past the timeout.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Mapping, Sequence

from ucla_agent.core.paths import repository_root

SIDES = ("p1", "p2")
SIM_SCRIPT = Path("tools") / "pokemon_sim" / "sim.js"
ANSWER_TIMEOUT_SECONDS = 30.0
CLOSE_TIMEOUT_SECONDS = 5.0
# How many of the helper's last stderr lines an error quotes.
STDERR_LINES_KEPT = 40
_ENCODING = "utf-8"


def default_command() -> list[str]:
    """The real helper: Showdown on Node, from the project's `tools/pokemon_sim` (after `npm ci` there)."""
    return ["node", str(repository_root() / SIM_SCRIPT)]


class BattleHelperError(RuntimeError):
    """The battle helper refused a command, died, or did not answer in time."""


@dataclass(frozen=True)
class SideUpdate:
    """What one side received since the previous answer: log lines, its latest request, an error's text."""

    log: tuple[str, ...]
    request: Mapping[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class HelperUpdate:
    """One answer of the helper: each side's update, whether the battle is over, the winner's name (None: a tie)."""

    sides: Mapping[str, SideUpdate]
    ended: bool
    winner: str | None


def parse_answer(answer: Mapping[str, Any]) -> HelperUpdate:
    """An answer line's object as a `HelperUpdate`; an `{"ok": false}` answer raises `BattleHelperError`."""
    if not answer.get("ok"):
        raise BattleHelperError(f"the battle helper refused a command: {answer.get('error')}")
    sides = {
        side: SideUpdate(tuple(seen["log"]), seen["request"], seen["error"]) for side, seen in answer["sides"].items()
    }
    return HelperUpdate(sides, bool(answer["ended"]), answer["winner"])


class BattleHelper:
    """A running battle helper; use it as a context manager so the process is always stopped."""

    def __init__(self, command: Sequence[str] | None = None, timeout_seconds: float = ANSWER_TIMEOUT_SECONDS) -> None:
        self.command = list(command) if command is not None else default_command()
        self._timeout = timeout_seconds
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding=_ENCODING,
            )
        except OSError as exc:
            raise BattleHelperError(f"cannot start the battle helper {self.command}: {exc}") from exc
        self._answers: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES_KEPT)
        self._threads = [
            threading.Thread(target=_pump, args=(self._process.stdout, self._answers.put), daemon=True),
            threading.Thread(target=_pump, args=(self._process.stderr, self._stderr.append), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def __enter__(self) -> BattleHelper:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self, battle_format: str, seed: str, players: Mapping[str, Mapping[str, Any]]) -> HelperUpdate:
        """Start the battle: `players` maps each side to its name and team (Showdown set objects)."""
        return self._ask({"command": "start", "format": battle_format, "seed": seed, "players": players})

    def choose(self, side: str, choice: str) -> HelperUpdate:
        """Send one side's choice, in Showdown's choice syntax."""
        return self._ask({"command": "choose", "side": side, "choice": choice})

    def close(self) -> None:
        """Close the helper's input and wait for it to exit; stop it if it does not."""
        if self._process.stdin and not self._process.stdin.closed:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def _ask(self, command: Mapping[str, Any]) -> HelperUpdate:
        self._send(command)
        try:
            line = self._answers.get(timeout=self._timeout)
        except queue.Empty as exc:
            raise BattleHelperError(f"no answer within {self._timeout} s; {self._stderr_text()}") from exc
        if line is None:
            raise BattleHelperError(f"the battle helper exited; {self._stderr_text()}")
        return parse_answer(json.loads(line))

    def _send(self, command: Mapping[str, Any]) -> None:
        try:
            self._process.stdin.write(json.dumps(command) + "\n")
            self._process.stdin.flush()
        except OSError as exc:
            raise BattleHelperError(f"the battle helper is gone: {exc}; {self._stderr_text()}") from exc

    def _stderr_text(self) -> str:
        # The stderr reader ends its lines with None, like the answer reader.
        return "stderr: " + ("".join(line for line in self._stderr if line).strip() or "(nothing)")


def _pump(stream: IO[str], put: Any) -> None:
    """Hand each line of a stream to `put`; then None, so a reader learns the stream ended."""
    for line in stream:
        put(line)
    put(None)
