"""The Werewolf tuning file a benchmark run plays with, read as strictly as the tracked one, and its SHA-256.

By default every Werewolf agent of a run plays `config/werewolf/tuning.toml`.
With `--tuning <path>` every one of them, our seat's and the `self` mix's
copies alike, plays the file at `<path>` instead, so two runs that differ in
one setting meet the same deals without the tracked file being edited.

The file is read by the tracked file's own strict reader, which reads the
tuning file under a project root: its bytes are copied to that place under a
temporary root, and an error names the given path. `summary.json` records
the path and the SHA-256 of the very bytes that were read.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ucla_agent.core.config import ConfigError
from ucla_agent.games.werewolf.tuning import TUNING_FILE, Tuning


@dataclass(frozen=True)
class RunTuning:
    """The tuning a run plays: the path it was read from, as given, the SHA-256 of its bytes and what it holds."""

    path: str
    sha256: str
    tuning: Tuning

    def described(self) -> dict[str, str]:
        """What `summary.json` records of the file: its path and its SHA-256."""
        return {"path": self.path, "sha256": self.sha256}


def run_tuning(path: Path | None, root: Path) -> RunTuning:
    """The tuning file at `path`, or the tracked one under `root` when `path` is None.

    Raises `ConfigError`, naming the file, when it cannot be read or is not a
    usable tuning file.
    """
    source, shown = (root / TUNING_FILE, TUNING_FILE.as_posix()) if path is None else (path, str(path))
    content = _read(source)
    return RunTuning(shown, hashlib.sha256(content).hexdigest(), _parsed(source, content))


def _read(source: Path) -> bytes:
    """The bytes of the file; `ConfigError` naming it when it cannot be read."""
    try:
        return source.read_bytes()
    except OSError as exc:
        raise ConfigError(f"The tuning file {source} cannot be read: {exc.strerror or exc}.") from exc


def _parsed(source: Path, content: bytes) -> Tuning:
    """The tuning `content` holds, read where the strict reader looks for it; its errors name `source`."""
    with tempfile.TemporaryDirectory() as staging_root:
        staged = Path(staging_root) / TUNING_FILE
        staged.parent.mkdir(parents=True)
        staged.write_bytes(content)
        try:
            return Tuning.from_file(Path(staging_root))
        except ConfigError as exc:
            raise ConfigError(str(exc).replace(str(staged), str(source))) from exc
