"""The submission package: the submission check, the documents it relies on, and the reference refresh tool."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
CHECK_SCRIPT = "check_submission.py"
# Git in a temporary repository, whatever the machine's own git settings say.
GIT_ENVIRONMENT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
GIT_IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid")
FIVE_MIB = 5 * 1024 * 1024
SIX_MIB = 6 * 1024 * 1024
STRONG_MODEL_FILE = "Qwen3.8-27B-Q8_0.gguf"
# Put together at run time, so that this file holds no literal the tracked-file scan would report.
AGENT_KEY = "eak_" + "live_" + "abcdefgh12345678"
# A row of the components page's model-role table: | `<role>` | `<file>` | <source with a link> | GGUF `<quant>` |
MODEL_ROLE_ROW = re.compile(
    r"^\| `(\w+)` \| `([^`]+)` \| [^|]*<https://huggingface\.co/[^>]+>[^|]* \| GGUF `([^`]+)` \|", re.MULTILINE
)
# A row of a reference-copy table: | `<file>` | <source, with a link> | <date retrieved> |
REFERENCE_ROW = re.compile(r"^\| `([^`]+)` \| [^|]*<https://[^>]+>[^|]* \| (\d{4}-\d\d-\d\d) \|$", re.MULTILINE)
# A platform doc's Retrieved date is the day of the download its copy came from: the latest download for every
# copy but the ones it left unchanged, which keep the date of the download before.
PLATFORM_DOCS_RETRIEVED = "2026-10-10"
PLATFORM_DOCS_KEPT_FROM_AN_EARLIER_DOWNLOAD = {"06-messaging.md": "2026-10-06"}
# A row of a games table: | <game> | `<platform game type>` | `<status>` | ...
GAME_STATUS_ROW = re.compile(
    r"^\|\s*(?P<game>[^|]+?)\s*\|\s*`(?P<game_type>[a-z_]+)`\s*\|\s*`(?P<status>[^`]+)`\s*\|", re.MULTILINE
)
# A playbook game section that opens with its status: ## <game>, a blank line, Status: `<status>`.
PLAYBOOK_STATUS = re.compile(r"^## (?P<game>.+)\n\nStatus: `(?P<status>[^`]+)`\.", re.MULTILINE)
# The playbook's game sections that give their status as one of the games status page's values.
PLAYBOOK_GAMES_WITH_A_STATUS = ("Werewolf", "Pokémon VGC doubles draft", "Honor of Kings")
PLAYBOOK_RULES = (
    "Scripted code guarantees a legal, timely move.",
    "Models make short choices and write language.",
    "Reliability and never missing a match come before cleverness.",
    "Claims are checked in test matches.",
)
# The steps of the submission checklist, in the order a person takes them.
SUBMISSION_STEPS = (
    "git rebase --root --exec",
    "pytest",
    "python -m ucla_agent.check",
    "python scripts/scan_secrets.py",
    "python scripts/check_submission.py",
    "`seat_plans` in `[redalert.sparring]` is empty",
    "`enabled` in `[jev]`, and `model_plan_choice` and `model_commands` in `[redalert]`",
    "altruagent.runner._terminal_game_state",
    "nothing to commit, working tree clean",
    "git push -u origin <branch>",
    "git rev-parse HEAD",
    "Make no further pushes.",
    "Practice may continue only on the frozen commit.",
)
LOOPBACK = "127.0.0.1"
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
HTTP_NOT_FOUND = 404
HTTP_OK = 200
SAME_CONTENT = b"# same\r\nbytes that did not change\n"
OLD_CONTENT = b"# differs\nthe copy kept in the repository\n"
NEW_CONTENT = b"# differs\nthe copy the platform serves today\n"


# -- a stub of the platform's web server -----------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """Serves the bytes the test put under a path, and 404 for any other path."""

    def do_GET(self) -> None:  # noqa: N802 - the name http.server looks for
        self.server.requests.append(self.path)
        body = self.server.files.get(self.path)
        if body is None:
            self.send_response(HTTP_NOT_FOUND)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTP_OK)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the signature http.server calls
        """Stay quiet: a test reads only what the tool prints."""


@pytest.fixture
def stub_server() -> Iterator[ThreadingHTTPServer]:
    """A web server on the loopback interface; set `files` (path -> bytes) and read `requests` (paths asked for)."""
    server = ThreadingHTTPServer((LOOPBACK, 0), _StubHandler)
    server.files = {}
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def direct_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """No proxy of the machine's environment stands between a tool and the stub server."""
    for name in PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", LOOPBACK)


def _url(server: ThreadingHTTPServer, path: str) -> str:
    return f"http://{LOOPBACK}:{server.server_port}{path}"


def _reference_tree(root: Path, rows: list[tuple[str, str]], current: dict[str, bytes]) -> Path:
    """A `docs/reference/` under `root`: an index listing `rows` (file name, URL) and the current copy's files."""
    folder = root / "docs" / "reference"
    (folder / "altruagent").mkdir(parents=True)
    for name, content in current.items():
        (folder / "altruagent" / name).write_bytes(content)
    table = "\n".join(f"| `{name}` | <{url}> | 2026-10-06 |" for name, url in rows)
    index = (
        "# Reference copies\n\n## Platform docs\n\n| File | Source | Retrieved |\n|---|---|---|\n"
        f"{table}\n\nThis table is the list the script reads.\n\n"
        "## Official rules\n\n| File | Source | Retrieved |\n|---|---|---|\n"
        "| `rules.pdf` | <https://rules.example.invalid/rules.pdf> | 2026-10-06 |\n"
    )
    (folder / "README.md").write_text(index, encoding="utf-8")
    return folder


def _folder_bytes(folder: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in folder.iterdir()}


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


@pytest.fixture
def refresh_tool(load_script):
    return load_script("refresh_reference.py")


@pytest.fixture
def two_docs(tmp_path: Path, stub_server: ThreadingHTTPServer) -> Path:
    """A reference tree listing one doc the platform still serves unchanged and one it serves changed."""
    stub_server.files = {"/skill/same": SAME_CONTENT, "/skill/differs": NEW_CONTENT}
    rows = [("same.md", _url(stub_server, "/skill/same")), ("differs.md", _url(stub_server, "/skill/differs"))]
    return _reference_tree(tmp_path, rows, {"same.md": SAME_CONTENT, "differs.md": OLD_CONTENT})


# -- scripts/refresh_reference.py ------------------------------------------------------------------


def test_refresh_writes_a_dated_folder_reports_each_file_and_leaves_the_current_copy_alone(
    tmp_path, two_docs, stub_server, run_script, direct_connections
):
    before = _utc_today()
    completed = run_script(tmp_path, "refresh_reference.py")
    after = _utc_today()

    (dated,) = [path for path in two_docs.iterdir() if path.name.startswith("altruagent-")]
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines()[:2] == ["same.md: unchanged", "differs.md: changed"]
    assert dated.name in {f"altruagent-{day:%Y%m%d}" for day in (before, after)}
    assert _folder_bytes(dated) == {"same.md": SAME_CONTENT, "differs.md": NEW_CONTENT}
    assert _folder_bytes(two_docs / "altruagent") == {"same.md": SAME_CONTENT, "differs.md": OLD_CONTENT}
    assert stub_server.requests == ["/skill/same", "/skill/differs"]


def test_refresh_names_the_dated_folder_and_says_the_current_copy_is_untouched(
    tmp_path, two_docs, refresh_tool, monkeypatch, capsys, direct_connections
):
    monkeypatch.chdir(tmp_path)

    exit_code = refresh_tool.main([])

    lines = capsys.readouterr().out.splitlines()
    assert exit_code == refresh_tool.EXIT_DOWNLOADED
    assert lines[-1].startswith("Downloaded into docs/reference/altruagent-")
    assert lines[-1].endswith("/; docs/reference/altruagent/ is untouched.")


def test_a_download_that_fails_is_reported_and_the_others_still_land(
    tmp_path, stub_server, refresh_tool, monkeypatch, capsys, direct_connections
):
    stub_server.files = {"/skill/served": NEW_CONTENT}
    rows = [("gone.md", _url(stub_server, "/skill/gone")), ("served.md", _url(stub_server, "/skill/served"))]
    folder = _reference_tree(tmp_path, rows, {})
    monkeypatch.chdir(tmp_path)

    exit_code = refresh_tool.main([])

    lines = capsys.readouterr().out.splitlines()
    (dated,) = [path for path in folder.iterdir() if path.name.startswith("altruagent-")]
    assert exit_code == refresh_tool.EXIT_DOWNLOAD_FAILED
    assert lines[:2] == ["gone.md: failed (HTTP 404)", "served.md: changed"]
    assert _folder_bytes(dated) == {"served.md": NEW_CONTENT}


def test_an_existing_dated_folder_stops_the_refresh_before_any_download(
    tmp_path, two_docs, stub_server, refresh_tool, monkeypatch, capsys, direct_connections
):
    refresh_tool.dated_folder(two_docs, _utc_today()).mkdir()
    monkeypatch.chdir(tmp_path)

    exit_code = refresh_tool.main([])

    captured = capsys.readouterr()
    assert exit_code == refresh_tool.EXIT_NOT_STARTED
    assert captured.out == ""
    assert "already exists" in captured.err
    assert stub_server.requests == []


def test_a_missing_index_stops_the_refresh(tmp_path, refresh_tool, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = refresh_tool.main([])

    assert exit_code == refresh_tool.EXIT_NOT_STARTED
    assert "README.md cannot be read" in capsys.readouterr().err


@pytest.mark.parametrize(
    "index, problem",
    [
        ("# Reference copies\n\nNo table here.\n", "lists no platform doc"),
        ("## Platform docs\n\n| `../escape.md` | <http://127.0.0.1:9/x> | 2026-10-06 |\n", "../escape.md"),
        (
            "## Official rules\n\n| `rules.pdf` | <https://rules.example.invalid/rules.pdf> | 2026-10-06 |\n",
            "lists no platform doc",
        ),
    ],
    ids=["no table", "a name with a folder", "a row under another heading"],
)
def test_an_index_without_a_usable_platform_doc_table_is_refused(refresh_tool, index, problem):
    with pytest.raises(refresh_tool.NotStartedError, match=problem.replace(".", r"\.")):
        refresh_tool.listed_files(index)


def test_a_server_that_cannot_be_reached_is_one_failed_line(tmp_path, refresh_tool):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused\nby the host", request=request)

    folder = _reference_tree(tmp_path, [("away.md", "https://docs.example.invalid/skill/away")], {})

    with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
        (outcome,) = refresh_tool.refresh(folder, date(2026, 10, 7), client)

    assert outcome.line == "away.md: failed (connection refused by the host)"
    assert not (folder / "altruagent-20261007" / "away.md").exists()
    assert not (folder / "altruagent-20261007" / "away.md").exists()


def test_a_doc_without_a_current_copy_counts_as_changed(tmp_path, refresh_tool):
    assert refresh_tool.compare(SAME_CONTENT, tmp_path / "absent.md") == refresh_tool.CHANGED


def test_refresh_returns_what_came_of_each_file_in_index_order(tmp_path, two_docs, refresh_tool):
    with httpx.Client(trust_env=False) as client:
        outcomes = refresh_tool.refresh(two_docs, date(2026, 10, 7), client)

    assert [outcome.line for outcome in outcomes] == ["same.md: unchanged", "differs.md: changed"]
    assert (two_docs / "altruagent-20261007" / "differs.md").read_bytes() == NEW_CONTENT


def test_the_repository_index_lists_exactly_the_files_of_the_current_copy(refresh_tool):
    index = (REPO_ROOT / "docs" / "reference" / "README.md").read_text(encoding="utf-8")
    current_copy = REPO_ROOT / "docs" / "reference" / "altruagent"

    listed = refresh_tool.listed_files(index)

    assert sorted(listed_file.name for listed_file in listed) == sorted(
        path.name for path in current_copy.iterdir() if path.is_file()
    )
    assert all(listed_file.url.startswith("https://") for listed_file in listed)


# -- a copy of the repository to break -------------------------------------------------------------


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *GIT_IDENTITY, *arguments],
        cwd=root,
        env={**os.environ, **GIT_ENVIRONMENT},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def pristine_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A git repository holding, committed, the working-tree content of every file this repository tracks."""
    copy = tmp_path_factory.mktemp("pristine") / "repository"
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, check=True, capture_output=True).stdout
    for name in filter(None, listed.decode("utf-8").split("\0")):
        source = REPO_ROOT / name
        if source.is_file():
            (copy / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copy / name)
    _git(copy, "init", "--quiet")
    _git(copy, "add", "--all")
    _git(copy, "commit", "--quiet", "--message", "copy")
    return copy


@pytest.fixture
def repository_copy(pristine_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh copy of the pristine repository for one test to break; git in it ignores the machine's settings."""
    copy = tmp_path / "repository"
    shutil.copytree(pristine_copy, copy)
    for name, value in GIT_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return copy


@pytest.fixture
def check_tool(load_script):
    return load_script(CHECK_SCRIPT)


@pytest.fixture
def run_check(check_tool, monkeypatch, capsys) -> Callable[[Path], tuple[int, list[str]]]:
    """Runs the check in this process on a folder: its exit code and the failure lines it printed."""

    def run(root: Path) -> tuple[int, list[str]]:
        monkeypatch.chdir(root)
        exit_code = check_tool.main([])
        return exit_code, capsys.readouterr().out.splitlines()[:-1]

    return run


def _failures(completed: subprocess.CompletedProcess) -> list[str]:
    """The failure lines of a run of the check: every line but the closing one."""
    return completed.stdout.splitlines()[:-1]


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def _track(root: Path, relative_path: str, content: bytes) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _git(root, "add", "--force", "--", relative_path)


# -- scripts/check_submission.py: the acceptance cases ---------------------------------------------


def test_the_check_passes_on_this_repository(run_script):
    completed = run_script(REPO_ROOT, CHECK_SCRIPT)

    assert (completed.returncode, completed.stdout) == (0, "Submission check passed.\n"), completed.stdout


def test_the_check_passes_on_an_unbroken_copy(repository_copy, run_check):
    assert run_check(repository_copy) == (0, [])


def test_deleting_the_components_page_fails_the_check_naming_it(repository_copy, run_script):
    (repository_copy / "docs" / "COMPONENTS.md").unlink()

    completed = run_script(repository_copy, CHECK_SCRIPT)

    assert completed.returncode == 1
    assert _failures(completed) == ["docs/COMPONENTS.md: missing"]
    assert completed.stdout.splitlines()[-1] == "Submission check failed: 1 problem(s)."


def test_a_model_role_missing_from_the_models_page_fails_the_check_naming_the_role(repository_copy, run_script):
    _append(repository_copy / "config" / "models.toml", '\n[roles.vision]\nmodel_path = "C:/Models/gguf/see.gguf"\n')

    completed = run_script(repository_copy, CHECK_SCRIPT)

    assert completed.returncode == 1
    assert _failures(completed) == ["config/models.toml: model role `vision` has no section in docs/MODELS.md"]


def test_a_tracked_file_over_5_mib_fails_the_check_naming_the_file(repository_copy, run_script):
    _track(repository_copy, "assets/big.bin", bytes(SIX_MIB))

    completed = run_script(repository_copy, CHECK_SCRIPT)

    assert completed.returncode == 1
    assert _failures(completed) == ["assets/big.bin: tracked file of 6,291,456 bytes, over the 5 MiB limit"]


def test_a_file_of_exactly_5_mib_is_allowed(repository_copy, run_check):
    _track(repository_copy, "assets/limit.bin", bytes(FIVE_MIB))

    assert run_check(repository_copy) == (0, [])


# -- scripts/check_submission.py: every other failure ----------------------------------------------


def _empty_playbook(root: Path) -> None:
    (root / "docs" / "PLAYBOOK.md").write_bytes(b"")


def _untrack_checklist(root: Path) -> None:
    _git(root, "rm", "--cached", "--quiet", "docs/SUBMISSION.md")


def _delete_readme(root: Path) -> None:
    (root / "README.md").unlink()


@pytest.mark.parametrize(
    "break_it, failure",
    [
        (_empty_playbook, "docs/PLAYBOOK.md: empty"),
        (_untrack_checklist, "docs/SUBMISSION.md: not tracked by git"),
        (_delete_readme, "README.md: missing"),
    ],
    ids=["empty", "untracked", "missing"],
)
def test_each_required_document_must_exist_be_non_empty_and_be_tracked(repository_copy, run_check, break_it, failure):
    break_it(repository_copy)

    assert run_check(repository_copy) == (1, [failure])


def test_a_path_the_components_page_names_must_be_in_the_repository(repository_copy, run_check):
    (repository_copy / "src" / "ucla_agent" / "draft.py").write_text("DRAFT = 1\n", encoding="utf-8")
    _append(
        repository_copy / "docs" / "COMPONENTS.md",
        "\nAlso `src/ucla_agent/nowhere.py`, `src/ucla_agent/draft.py` and the ignored `runs/model-server/`.\n",
    )

    assert run_check(repository_copy) == (
        1,
        [
            "docs/COMPONENTS.md names src/ucla_agent/nowhere.py, which is not in the repository",
            "docs/COMPONENTS.md names src/ucla_agent/draft.py, which git does not track",
        ],
    )


def test_a_prompt_template_the_components_page_does_not_name_fails_the_check(repository_copy, run_check):
    template = "src/ucla_agent/games/redalert/prompts/new_question.txt"
    (repository_copy / template).write_text("Which plan?\n", encoding="utf-8")

    assert run_check(repository_copy) == (1, [f"{template}: prompt template not named in docs/COMPONENTS.md"])


def _other_model_file(root: Path) -> None:
    _replace(root / "docs" / "MODELS.md", f"| Model file | `{STRONG_MODEL_FILE}` |", "| Model file | `other.gguf` |")


def _no_model_file_row(root: Path) -> None:
    _replace(root / "docs" / "MODELS.md", f"| Model file | `{STRONG_MODEL_FILE}` |\n", "")


def _role_without_model_path(root: Path) -> None:
    _append(root / "config" / "models.toml", "\n[roles.vision]\nrequired = false\n")


def _role_without_section(root: Path) -> None:
    _replace(root / "docs" / "MODELS.md", "### `strong`", "### `stronger`")


@pytest.mark.parametrize(
    "break_it, failure",
    [
        (_other_model_file, f"model role `strong` uses {STRONG_MODEL_FILE}, but docs/MODELS.md gives other.gguf"),
        (_no_model_file_row, f"model role `strong` uses {STRONG_MODEL_FILE}, but docs/MODELS.md gives no model file"),
        (_role_without_model_path, "model role `vision` has no model_path"),
        (_role_without_section, "model role `strong` has no section in docs/MODELS.md"),
    ],
    ids=["another file", "no file row", "no model path", "no section"],
)
def test_each_model_role_must_name_the_same_file_on_the_models_page(repository_copy, run_check, break_it, failure):
    break_it(repository_copy)

    assert run_check(repository_copy) == (1, [f"config/models.toml: {failure}"])


def test_an_unreadable_models_configuration_is_a_failure(repository_copy, run_check):
    (repository_copy / "config" / "models.toml").write_text("[roles.fast\n", encoding="utf-8")

    exit_code, failures = run_check(repository_copy)

    assert exit_code == 1
    assert len(failures) == 1 and failures[0].startswith("model roles cannot be checked: ")


def test_a_tracked_file_under_a_local_only_folder_fails_the_check(repository_copy, run_check):
    _track(repository_copy, "runs/20261007/run.jsonl", b"{}\n")

    assert run_check(repository_copy) == (
        1,
        [
            "runs/20261007/run.jsonl: tracked file under runs/, which must stay local",
            "runs/20261007/run.jsonl: tracked file that .gitignore ignores",
        ],
    )


def test_a_tracked_file_that_gitignore_ignores_fails_the_check(repository_copy, run_check):
    _track(repository_copy, ".env", b"ALTRUAGENT_CONTROL_URL=http://127.0.0.1:9\n")

    assert run_check(repository_copy) == (1, [".env: tracked file that .gitignore ignores"])


def test_a_pinned_sparring_plan_fails_the_check(repository_copy, run_check):
    _replace(repository_copy / "config" / "agent.toml", "seat_plans = {}", 'seat_plans = {Multi1 = "HOLD"}')

    assert run_check(repository_copy) == (
        1,
        [
            "config/agent.toml: [redalert.sparring] seat_plans is not empty ({'Multi1': 'HOLD'}); "
            "it must be empty to submit"
        ],
    )


def test_an_unreadable_agent_configuration_is_a_failure(repository_copy, run_check):
    (repository_copy / "config" / "agent.toml").write_text("[redalert\n", encoding="utf-8")

    exit_code, failures = run_check(repository_copy)

    assert exit_code == 1
    assert len(failures) == 1 and failures[0].startswith("config/agent.toml cannot be read: ")


def test_a_secret_in_a_tracked_file_fails_the_check_by_path_and_line(repository_copy, run_check):
    _track(repository_copy, "notes/leak.txt", f"first line\nkey = {AGENT_KEY}\n".encode("utf-8"))

    exit_code, failures = run_check(repository_copy)

    assert (exit_code, failures) == (1, ["scripts/scan_secrets.py found a secret at notes/leak.txt:2"])


def test_a_secret_scan_that_cannot_run_fails_the_check(repository_copy, run_check):
    (repository_copy / "scripts" / "scan_secrets.py").unlink()

    exit_code, failures = run_check(repository_copy)

    assert exit_code == 1
    assert len(failures) == 1 and failures[0].startswith("scripts/scan_secrets.py failed with exit code 2: ")


def test_outside_a_git_repository_the_check_fails_saying_why(tmp_path, run_check, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    exit_code, failures = run_check(tmp_path)

    assert exit_code == 1
    assert len(failures) == 1 and failures[0].startswith("The files git tracks cannot be listed: ")


def test_without_git_on_the_path_the_check_says_git_cannot_be_run(tmp_path, check_tool, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    (failure,) = check_tool.check(tmp_path)

    assert failure.startswith("The files git tracks cannot be listed: git cannot be run: ")


def test_an_empty_repository_lacks_every_document(tmp_path, check_tool, monkeypatch):
    for name, value in GIT_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    _git(tmp_path, "init", "--quiet")

    failures = check_tool.check(tmp_path)

    assert failures[:5] == [f"{document}: missing" for document in check_tool.DOCUMENTS]


def test_a_submodule_entry_has_no_size_and_does_not_fail_the_check(repository_copy, run_check):
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository_copy, check=True, capture_output=True, text=True
    ).stdout.strip()
    _git(repository_copy, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor/linked")

    assert run_check(repository_copy) == (0, [])


def test_a_path_git_cannot_judge_stops_the_check_with_git_s_reason(tmp_path, check_tool, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    with pytest.raises(check_tool.GitError, match="git check-ignore failed for runs/"):
        check_tool._is_ignored(tmp_path, "runs/")


def test_a_git_failure_during_the_check_is_printed_as_the_one_failure(check_tool, monkeypatch, capsys, tmp_path):
    def fail(_root: Path) -> list[str]:
        raise check_tool.GitError("git ls-files failed in the test")

    monkeypatch.setattr(check_tool, "check", fail)
    monkeypatch.chdir(tmp_path)

    exit_code = check_tool.main([])

    assert exit_code == check_tool.EXIT_FAILED
    assert capsys.readouterr().out.splitlines() == [
        "git ls-files failed in the test",
        "Submission check failed: 1 problem(s).",
    ]


# -- scripts/check_submission.py: its rules --------------------------------------------------------


def test_only_relative_repository_paths_count_as_named_paths(check_tool):
    page = (
        "`src/altruagent/` `config/agent.toml` `src/altruagent/` `.env` `http://127.0.0.1:11500` "
        "`C:/Models/gguf/x.gguf` `runs/model-server/<role>.log` `POST /v1/models` `/v1/models` "
        "`ucla_agent.core.records.finish_match` `local:<role>` `../outside/x.md` `./README.md` `src//x.py`"
    )

    assert check_tool.named_paths(page) == ["src/altruagent/", "config/agent.toml"]


@pytest.mark.parametrize(
    "path, folder",
    [
        ("runs/20261007/run.jsonl", "runs/"),
        ("docs/reference/research/papers/a.pdf", "docs/reference/research/"),
        ("src/ucla_agent/__pycache__/x.pyc", "__pycache__/"),
        ("tests/runs.py", None),
        ("src/tmp_helper.py", None),
        ("docs/reference/altruagent/skill.md", None),
    ],
)
def test_a_local_only_folder_is_matched_as_gitignore_would(check_tool, path, folder):
    assert check_tool._local_only_folder(path) == folder


def test_the_prompt_templates_of_this_repository_are_found(check_tool):
    assert check_tool.prompt_templates(REPO_ROOT) == [
        "src/ucla_agent/games/redalert/prompts/plan_choice.txt",
        "src/ucla_agent/games/redalert/prompts/plan_choice_briefing.txt",
    ]


def test_the_local_only_folders_are_ones_gitignore_lists(check_tool):
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert [folder for folder in check_tool.LOCAL_ONLY_FOLDERS if folder not in gitignore] == []


# -- .gitignore and the documents ------------------------------------------------------------------


def _plain(path: Path) -> str:
    """A document with markdown emphasis removed and runs of whitespace collapsed, so wrapping does not matter."""
    return " ".join(path.read_text(encoding="utf-8").replace("*", "").split())


@pytest.mark.parametrize(
    "entry",
    [
        ".env",
        "runs/",
        "tmp/",
        "docs/reference/research/",
        "docs/reference/altruagent-starter/",
        "__pycache__/",
        ".venv/",
        ".agent.lock",
    ],
)
def test_gitignore_lists_every_entry_the_requirement_names(entry):
    assert entry in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    "text",
    [
        "python -m venv .venv",
        'pip install -e ".[dev]"',
        "Copy-Item .env.example .env",
        "cp .env.example .env",
        "docs/MODELS.md",
        "python -m ucla_agent.check",
        "python -m agent --check-tournament",
        "python -m agent --match",
        "python -m agent --tournament --match",
        "| Red Alert | `red_alert` |",
        "| Werewolf | `werewolf` |",
        "| Pokémon VGC doubles draft | `pokemon_vgc_doubles_draft` |",
        "| Honor of Kings | `honor_of_kings` |",
    ],
)
def test_the_readme_gives_setup_checks_games_and_how_to_start(text):
    assert text in _plain(REPO_ROOT / "README.md")


def _game_statuses(path: Path, key: str) -> dict[str, str]:
    """Each game's status in a page's games table, by the game's name (`game`) or platform game type (`game_type`)."""
    return {row[key]: row["status"] for row in GAME_STATUS_ROW.finditer(path.read_text(encoding="utf-8"))}


def test_the_readme_games_table_gives_each_game_the_status_the_games_status_page_gives_it():
    readme = _game_statuses(REPO_ROOT / "README.md", "game_type")

    assert readme == _game_statuses(DOCS / "GAMES-STATUS.md", "game_type")
    assert len(readme) == 4


@pytest.mark.parametrize(
    "heading",
    [
        "## Vendored starter runtime",
        "### The one attachment to a non-public part of the runtime",
        "## Entry point and dispatcher",
        "## Red Alert scripted core",
        "## Compact shorthand",
        "## Included model server",
        "### Model roles",
        "## Decision sources",
        "### Jev",
        "## Prompts",
        "## Excluded items",
    ],
)
def test_the_components_page_has_a_section_for_each_component_the_requirement_names(heading):
    assert heading in (DOCS / "COMPONENTS.md").read_text(encoding="utf-8").splitlines()


def test_the_components_page_gives_each_model_role_its_file_source_and_quantization():
    page = (DOCS / "COMPONENTS.md").read_text(encoding="utf-8")

    roles = MODEL_ROLE_ROW.findall(page)

    assert [role for role, _model_file, _quantization in roles] == ["fast", "strong", "small"]
    assert all(model_file.endswith(f"{quantization}.gguf") for _role, model_file, quantization in roles)


def test_the_components_page_says_whether_jev_is_enabled_and_lists_the_excluded_items():
    page = _plain(DOCS / "COMPONENTS.md")

    assert "| Enabled | No. `enabled = false` as shipped" in page
    for excluded in ("Keys:", "Model weight files", "Downloaded research material", "The local OpenRA test harness"):
        assert f"| {excluded}" in page
    assert "Local testing used a separate harness, not needed to run the agent" in page


def test_the_playbook_gives_the_guidance_and_one_section_per_game():
    playbook = _plain(DOCS / "PLAYBOOK.md")
    headings = re.findall(r"^## (.+)$", (DOCS / "PLAYBOOK.md").read_text(encoding="utf-8"), re.MULTILINE)

    assert [rule for rule in PLAYBOOK_RULES if rule not in playbook] == []
    assert headings == [
        "Guidance for every game",
        "Red Alert",
        "Werewolf",
        "Pokémon VGC doubles draft",
        "Honor of Kings",
    ]
    assert "### Known weaknesses" in playbook and "won 1 of 15 recent games" in playbook


def test_each_playbook_game_section_opens_with_the_status_the_games_status_page_gives_the_game():
    sections = PLAYBOOK_STATUS.finditer((DOCS / "PLAYBOOK.md").read_text(encoding="utf-8"))
    statuses = _game_statuses(DOCS / "GAMES-STATUS.md", "game")

    assert {section["game"]: section["status"] for section in sections} == {
        game: statuses[game] for game in PLAYBOOK_GAMES_WITH_A_STATUS
    }


def test_the_submission_checklist_has_every_step_in_order():
    checklist = _plain(DOCS / "SUBMISSION.md")

    positions = [checklist.index(step) for step in SUBMISSION_STEPS]

    assert positions == sorted(positions)
    assert "before 11:59 p.m. Pacific on 2026-10-13" in checklist


def test_the_reference_index_names_each_kept_copy_and_the_local_only_folders():
    index = (DOCS / "reference" / "README.md").read_text(encoding="utf-8")
    folders = [DOCS / "reference" / "altruagent", DOCS / "reference" / "altruagent-rules"]
    kept = {path.name for folder in folders for path in folder.iterdir() if path.is_file()}

    assert {name for name, _retrieved in REFERENCE_ROW.findall(index)} == kept
    assert "Folder: `docs/reference/altruagent/`" in index and "Folder: `docs/reference/altruagent-rules/`" in index
    assert "`docs/reference/research/`" in index and "`docs/reference/altruagent-starter/`" in index
    assert "git ignores: they are never tracked" in _plain(DOCS / "reference" / "README.md")


def test_the_reference_index_dates_each_platform_doc_by_the_download_its_copy_came_from():
    index = (DOCS / "reference" / "README.md").read_text(encoding="utf-8")
    platform_docs = index.split("## Platform docs", 1)[1].split("\n## ", 1)[0]
    current_copy = DOCS / "reference" / "altruagent"

    assert dict(REFERENCE_ROW.findall(platform_docs)) == {
        path.name: PLATFORM_DOCS_KEPT_FROM_AN_EARLIER_DOWNLOAD.get(path.name, PLATFORM_DOCS_RETRIEVED)
        for path in current_copy.iterdir()
        if path.is_file()
    }
