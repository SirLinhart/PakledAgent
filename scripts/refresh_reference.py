"""Download the platform docs again and say, file by file, whether they changed.

    python scripts/refresh_reference.py

Run it from the repository root. It reads the table under `## Platform docs`
in `docs/reference/README.md`, one row per file of the current copy
`docs/reference/altruagent/` with the URL the file came from, downloads every
file into a new folder next to that copy, `docs/reference/altruagent-<yyyymmdd>/`
(the UTC date of the run), and prints one line per file:

    <file>: unchanged            the download's SHA-256 is the current copy's
    <file>: changed              it is not, or there is no current copy
    <file>: failed (<reason>)    the file could not be downloaded

It never writes into the current copy. A person compares the two folders and
decides what to take over (see `docs/reference/README.md`).

Exit code 0: every file was downloaded. 1: at least one download failed.
2: the list cannot be read, names something that is not a plain file name, or
the dated folder already exists; nothing is downloaded then.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

import httpx

from ucla_agent.core.console import print_line

EXIT_DOWNLOADED = 0
EXIT_DOWNLOAD_FAILED = 1
EXIT_NOT_STARTED = 2
REFERENCE_FOLDER = Path("docs") / "reference"
INDEX_FILE_NAME = "README.md"
CURRENT_COPY_NAME = "altruagent"
PLATFORM_DOCS_HEADING = "## Platform docs"
# The text under one second-level heading, up to the next one.
_SECTION = r"^{heading}$(?P<body>.*?)(?=^## |\Z)"
# A row of the platform-doc table: | `<file name>` | <<source URL>> | <date retrieved> |
LISTED_FILE = re.compile(r"^\| `(?P<name>[^`]+)` \| <(?P<url>https?://[^>]+)> \|", re.MULTILINE)
# A file name with no folder in it, so a download can only land in the dated folder.
PLAIN_FILE_NAME = re.compile(r"\w[\w.-]*")
DATED_FOLDER_FORMAT = "%Y%m%d"
DOWNLOAD_TIMEOUT_SECONDS = 30.0
UNCHANGED = "unchanged"
CHANGED = "changed"
FAILED = "failed"
_INDEX_ENCODING = "utf-8"


class NotStartedError(Exception):
    """The refresh cannot start; the message says why."""


class ListedFile(NamedTuple):
    """One platform doc: its file name in the current copy and the URL it is downloaded from."""

    name: str
    url: str


class Refreshed(NamedTuple):
    """What came of one file: `unchanged`, `changed` or `failed`, with the reason of a failure."""

    name: str
    status: str
    reason: str = ""

    @property
    def line(self) -> str:
        """The line printed for the file."""
        return f"{self.name}: {self.status} ({self.reason})" if self.reason else f"{self.name}: {self.status}"


def listed_files(index_text: str) -> list[ListedFile]:
    """The platform docs an index lists, in its order. Raises `NotStartedError` for no list or a name with a folder."""
    section = re.search(_SECTION.format(heading=re.escape(PLATFORM_DOCS_HEADING)), index_text, re.MULTILINE | re.DOTALL)
    files = [ListedFile(row["name"], row["url"]) for row in LISTED_FILE.finditer(section["body"] if section else "")]
    if not files:
        raise NotStartedError(f"The index lists no platform doc under {PLATFORM_DOCS_HEADING!r}")
    unsafe = [listed.name for listed in files if not PLAIN_FILE_NAME.fullmatch(listed.name)]
    if unsafe:
        raise NotStartedError(f"The index lists names that are not plain file names: {', '.join(unsafe)}")
    return files


def dated_folder(reference_folder: Path, run_date: date) -> Path:
    """The folder a refresh on that date downloads into, next to the current copy."""
    return reference_folder / f"{CURRENT_COPY_NAME}-{run_date.strftime(DATED_FOLDER_FORMAT)}"


def compare(download: bytes, current: Path) -> str:
    """`unchanged` when the current copy has the download's SHA-256; `changed` otherwise, also when there is none."""
    if not current.is_file():
        return CHANGED
    same = hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(download).digest()
    return UNCHANGED if same else CHANGED


def refresh(reference_folder: Path, run_date: date, client: httpx.Client) -> list[Refreshed]:
    """Download every listed doc into the dated folder and say what came of each, in the order of the index.

    Raises `NotStartedError`, before anything is downloaded, when the index
    cannot be read or the dated folder already exists.
    """
    index = reference_folder / INDEX_FILE_NAME
    try:
        files = listed_files(index.read_text(encoding=_INDEX_ENCODING))
    except OSError as exc:
        raise NotStartedError(f"{index} cannot be read: {exc}") from exc
    target = dated_folder(reference_folder, run_date)
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise NotStartedError(f"{target} already exists: delete it to refresh again today") from exc
    current = reference_folder / CURRENT_COPY_NAME
    return [_download(listed, current / listed.name, target / listed.name, client) for listed in files]


def _download(listed: ListedFile, current: Path, destination: Path, client: httpx.Client) -> Refreshed:
    """Download one doc to its place in the dated folder and compare it with the current copy."""
    try:
        response = client.get(listed.url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return Refreshed(listed.name, FAILED, f"HTTP {exc.response.status_code}")
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # httpx messages can run over several lines, and each file gets one line.
        return Refreshed(listed.name, FAILED, " ".join(str(exc).split()) or type(exc).__name__)
    destination.write_bytes(response.content)
    return Refreshed(listed.name, compare(response.content, current))


def main(arguments: list[str]) -> int:
    """Refresh the platform docs of the repository in the current directory; returns the exit code."""
    parser = argparse.ArgumentParser(prog="python scripts/refresh_reference.py", description=__doc__.split("\n\n")[0])
    parser.parse_args(arguments)
    reference_folder = Path.cwd() / REFERENCE_FOLDER
    run_date = datetime.now(timezone.utc).date()
    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
            outcomes = refresh(reference_folder, run_date, client)
    except NotStartedError as exc:
        print_line(str(exc), error=True)
        return EXIT_NOT_STARTED
    for outcome in outcomes:
        print_line(outcome.line)
    target = dated_folder(REFERENCE_FOLDER, run_date).as_posix()
    print_line(f"Downloaded into {target}/; {(REFERENCE_FOLDER / CURRENT_COPY_NAME).as_posix()}/ is untouched.")
    return EXIT_DOWNLOAD_FAILED if any(outcome.status == FAILED for outcome in outcomes) else EXIT_DOWNLOADED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
