"""The console writer: every line the agent prints is redacted, and nothing prints around it."""

from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

from ucla_agent.core.console import can_print, print_line
from ucla_agent.core.secrets import REDACTED

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PACKAGE = REPO_ROOT / "src" / "ucla_agent"
ENTRY_POINT_PACKAGE = REPO_ROOT / "src" / "agent"
SCRIPTS_FOLDER = REPO_ROOT / "scripts"
CONSOLE_MODULE = AGENT_PACKAGE / "core" / "console.py"
STANDARD_STREAMS = {"stdout", "stderr"}
# Put together at run time, so that this file holds no literal the tracked-file scan would report.
AGENT_KEY = "eak_" + "live_" + "abcdefgh12345678"
LOADED_KEY = "jev-4f9c2d7e1a6b8053"


def test_print_line_writes_one_line_to_standard_output(run_folder, capsys):
    print_line("Waiting for your next game...")

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("Waiting for your next game...\n", "")


def test_print_line_writes_to_standard_error_when_asked(run_folder, capsys):
    print_line("the result hook failed", error=True)

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "the result hook failed\n")


def test_print_line_redacts_a_loaded_key_and_a_secret_shaped_string(run_folder, monkeypatch, capsys):
    monkeypatch.setenv("JEV_API_KEY", LOADED_KEY)

    print_line(f"Jev refused {LOADED_KEY}; the platform sent {AGENT_KEY}")

    assert capsys.readouterr().out == f"Jev refused {REDACTED}; the platform sent {REDACTED}\n"


# -- what the console can show ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "encoding, text, printable",
    [
        ("utf-8", "✓ Ready", True),
        ("cp1252", "✓ Ready", False),
        ("cp1252", "[ok] Ready", True),
        ("ascii", "café", False),
    ],
    ids=["utf-8", "a mark cp1252 lacks", "plain text in cp1252", "an accent in ascii"],
)
def test_can_print_says_whether_the_console_s_encoding_has_every_character(monkeypatch, encoding, text, printable):
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding=encoding))

    assert can_print(text) is printable


def test_a_console_without_a_known_encoding_is_taken_to_be_ascii(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert (can_print("[ok]"), can_print("✓")) == (True, False)


def test_a_console_whose_encoding_python_does_not_know_can_print_nothing(monkeypatch):
    class _OddConsole(io.StringIO):
        encoding = "no-such-encoding"

    monkeypatch.setattr(sys, "stdout", _OddConsole())

    assert can_print("[ok]") is False


# -- nothing prints around the writer --------------------------------------------------------------


def _is_print_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"


def _is_standard_stream(node: ast.AST) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr not in STANDARD_STREAMS:
        return False
    return isinstance(node.value, ast.Name) and node.value.id == "sys"


def direct_console_writes(source: str) -> list[int]:
    """The lines of a Python source that call `print` or use `sys.stdout` / `sys.stderr` themselves."""
    nodes = ast.walk(ast.parse(source))
    return sorted(node.lineno for node in nodes if _is_print_call(node) or _is_standard_stream(node))


def _modules_that_must_use_the_writer() -> list[Path]:
    modules = [*AGENT_PACKAGE.rglob("*.py"), *ENTRY_POINT_PACKAGE.rglob("*.py"), *SCRIPTS_FOLDER.glob("*.py")]
    return sorted(path for path in modules if path != CONSOLE_MODULE)


def test_no_agent_module_entry_point_or_script_writes_to_the_console_itself():
    found = {
        path.relative_to(REPO_ROOT).as_posix(): lines
        for path in _modules_that_must_use_the_writer()
        if (lines := direct_console_writes(path.read_text(encoding="utf-8")))
    }

    assert found == {}


def test_the_scan_covers_the_agent_package_the_entry_point_and_the_scripts():
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _modules_that_must_use_the_writer()}

    assert {"src/ucla_agent/core/startup.py", "src/ucla_agent/core/records.py"} <= scanned
    assert {"src/agent/__main__.py", "src/agent/agent.py", "scripts/report.py"} <= scanned
    assert "src/ucla_agent/core/console.py" not in scanned


@pytest.mark.parametrize(
    "source, lines",
    [
        ("print('ready')\n", [1]),
        ("import sys\n\nsys.stdout.write('ready')\n", [3]),
        ("import sys\n\n\ndef warn(text):\n    print(text, file=sys.stderr)\n", [5, 5]),
        ("from ucla_agent.core.console import print_line\n\nprint_line('ready')\n", []),
        ("completed = run()\nmessage = completed.stderr.strip()\n", []),
    ],
    ids=["print", "sys.stdout", "print to sys.stderr", "the writer", "another object's stderr"],
)
def test_the_scan_finds_each_way_of_writing_to_the_console(source, lines):
    assert direct_console_writes(source) == lines
