"""Shipped roles on the real llama-server: how fast `small` answers the Red Alert plan choice, and with what, with a
short reason and without, and with the plain-words briefing and its reason first, and how much system memory
`strong` holds while it answers.

Not part of the default run: `pytest -m gpu tests/core/test_modelserver_gpu.py -k small`,
`pytest -m gpu tests/core/test_modelserver_gpu.py -k reason`,
`pytest -m gpu tests/core/test_modelserver_gpu.py -k briefing` and
`pytest -m gpu tests/core/test_modelserver_gpu.py -k no_mmap`. Each needs the GPU, its role's model file and the
llama-server the shipped configuration names, and the port 11500 and its role's backend port free.
"""

from __future__ import annotations

import ctypes
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import situations
from fake_game import STEPS_PER_SECOND
from scenarios.standard import StandardScenario
from ucla_agent.core import modelserver
from ucla_agent.core.decide import USED, ChoiceRequest, LocalModelSource, Option
from ucla_agent.core.decide import settings as decide_settings
from ucla_agent.core.decide.journal import SourceCall
from ucla_agent.core.modelserver import children
from ucla_agent.core.modelserver import settings as server_settings
from ucla_agent.core.stats import percentile
from ucla_agent.games.redalert import plans
from ucla_agent.games.redalert.briefing import Briefing
from ucla_agent.games.redalert.composition import PRESS
from ucla_agent.games.redalert.glossary import Glossary
from ucla_agent.games.redalert.groups import RAID
from ucla_agent.games.redalert.shorthand import summarize

REPO_ROOT = Path(__file__).resolve().parents[2]
SMALL = "small"
REQUESTS = 20
# The start of the standard scenario the agent plays, and the moment of the match the question is put at.
FACTION = "england"
ASKED_AT_SECONDS = 300
# The agent puts the plan choice to a model only when two plans or more are open.
MIN_OPEN_PLANS = 2
P50, P95 = 50, 95
# With a reason asked for as well, the plan choice still has to come well within its 1,500 ms deadline.
REASON_MEDIAN_LIMIT_MS = 600
REASON_MAX_LIMIT_MS = 1200
# With the briefing as the context and a reason of up to 30 words written before the choice, still within the
# plan choice's 1,500 ms deadline.
BRIEFING_MEDIAN_LIMIT_MS = 900
BRIEFING_MAX_LIMIT_MS = 1400
STRONG = "strong"
# What `strong` may hold in system memory while it answers: its weights are in VRAM, so only the runtime is.
RESIDENT_LIMIT_BYTES = 4 * 1024**3
COMPLETION_LIMIT_SECONDS = 30.0
SAMPLE_SECONDS = 0.05
BYTES_PER_GB = 1e9
# winnt.h access rights.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010


def played_for(seconds: int) -> Any:
    """The agent after playing the standard scenario for `seconds`."""
    agent = situations.own_agent()
    game = StandardScenario(lambda: agent.ctx.groups.members(RAID)).new_game(FACTION)
    # The first state is the one at 0 s, so the agent's last state is the one at `seconds`.
    game.run(agent, seconds * STEPS_PER_SECOND + 1)
    return agent


def plan_choice_at(seconds: int) -> ChoiceRequest:
    """The plan choice the agent puts to a model after playing the standard scenario for `seconds`.

    It is made of the agent's own pieces: the summary of the match, the tracked
    prompt and the plans open at that moment with their descriptions, held to
    the default deadline of the shipped `config/agent.toml`.
    """
    agent = played_for(seconds)
    world, ctx = agent.world, agent.ctx
    # When an enemy unit was last in sight opens no plan, so no sighting is given.
    open_plans = plans.open_plans(plans.observe(world, ctx, ctx.tuning, None), ctx.tuning.plan_rules)
    return ChoiceRequest(
        question=plans.plan_prompt(),
        options=[Option(plan, plans.PLAN_DESCRIPTIONS[plan]) for plan in open_plans],
        context=summarize(world, ctx.scout_report, ctx.plan, ctx.groups, ctx.threat_level),
        deadline_ms=decide_settings.load(REPO_ROOT).default_deadline_ms,
        state_tag=str(world.tick),
    )


def briefed_plan_choice_at(seconds: int) -> ChoiceRequest:
    """The plan choice the agent puts to a model with `model_briefing` on, after playing the standard scenario for
    `seconds`.

    It is made as the plan rules make it: the briefing of the match over the
    tracked glossary as the context, the briefing's question, the plans open
    at that moment with their descriptions, and a reason of at most 30 words
    written before the choice in at most 96 tokens, held to the plan choice's
    deadline.
    """
    agent = played_for(seconds)
    world, ctx = agent.world, agent.ctx
    # The briefing reads the figures the plan rules decided the last cycle on, the time of the last sighting among them.
    situation = agent._plan_selector.situation
    open_plans = plans.open_plans(situation, ctx.tuning.plan_rules, ctx.plan == PRESS)
    return ChoiceRequest(
        question=plans.plan_prompt(briefed=True),
        options=[Option(plan, plans.PLAN_DESCRIPTIONS[plan]) for plan in open_plans],
        context=Briefing(Glossary.from_file(REPO_ROOT)).write(world, ctx, situation, open_plans),
        deadline_ms=ctx.tuning.plan_rules.choice_deadline_ms,
        state_tag=str(world.tick),
        reason_first=True,
        reason_words=plans.BRIEFED_REASON_WORDS,
        max_answer_tokens=plans.BRIEFED_ANSWER_TOKENS,
    )


def write_small_role_only(root: Path) -> None:
    """Write the shipped `config/models.toml` into `root` with `small` as its one role, so only that model loads."""
    shipped = (REPO_ROOT / "config" / "models.toml").read_text(encoding="utf-8")
    server_tables = shipped.partition("[roles.")[0]
    heading = f"[roles.{SMALL}]"
    small_only = server_tables + heading + shipped.partition(heading)[2]
    (root / "config").mkdir()
    (root / "config" / "models.toml").write_bytes(small_only.encode("utf-8"))


def fallbacks(calls: list[SourceCall], open_plans: list[str]) -> list[SourceCall]:
    """The calls whose answer the plan selector does not take: not used, or not one of the open plans."""
    return [call for call in calls if call.outcome != USED or call.answer not in open_plans]


@pytest.mark.gpu
def test_small_role_plan_choice_latency(run_folder, local_http, capsys):
    """Twenty plan choices put to the shipped `small` role alone; prints p50 and p95 latency and the fallbacks."""
    request = plan_choice_at(ASKED_AT_SECONDS)
    open_plans = request.option_ids()
    assert len(open_plans) >= MIN_OPEN_PLANS, f"only {open_plans} open at {ASKED_AT_SECONDS} s"
    write_small_role_only(run_folder)
    served = server_settings.load(run_folder)
    assert [role.name for role in served.roles] == [SMALL]

    modelserver.start()
    try:
        health = local_http.get(f"{served.endpoint_url}/health").json()
        assert health == {"roles": {SMALL: {"state": "ready"}}}, run_folder / "runs" / "model-server" / f"{SMALL}.log"
        source = LocalModelSource(SMALL)
        calls = [call for _ in range(REQUESTS) for call in source.ask(request)]
    finally:
        modelserver.stop()

    latencies = [call.latency_ms for call in calls]
    answers = Counter(call.answer if call.outcome == USED else f"{call.outcome}:{call.reason}" for call in calls)
    with capsys.disabled():
        print(
            f"\nlocal:{SMALL}: {len(calls)} plan choices among {', '.join(open_plans)} at {ASKED_AT_SECONDS} s, "
            f"deadline {request.deadline_ms} ms: p50 {percentile(latencies, P50)} ms, "
            f"p95 {percentile(latencies, P95)} ms, fallbacks {len(fallbacks(calls, open_plans))}"
        )
        print(f"answers {dict(answers)}, latencies in order (ms) {latencies}")
    assert len(calls) == REQUESTS
    assert all(call.answer in open_plans for call in calls if call.outcome == USED)


@pytest.mark.gpu
def test_reason_with_each_plan_choice_keeps_the_median_at_most_600_ms_and_the_slowest_at_most_1200_ms(
    run_folder, local_http, capsys
):
    """Twenty plan choices put to the shipped `small` role alone with a short reason asked for; prints each answer.

    Every answer has to be used and come with a reason; the median latency
    has to be at most 600 ms and the slowest at most 1,200 ms.
    """
    request = plan_choice_at(ASKED_AT_SECONDS)
    open_plans = request.option_ids()
    assert len(open_plans) >= MIN_OPEN_PLANS, f"only {open_plans} open at {ASKED_AT_SECONDS} s"
    write_small_role_only(run_folder)
    served = server_settings.load(run_folder)

    modelserver.start()
    try:
        health = local_http.get(f"{served.endpoint_url}/health").json()
        assert health == {"roles": {SMALL: {"state": "ready"}}}, run_folder / "runs" / "model-server" / f"{SMALL}.log"
        source = LocalModelSource(SMALL, choice_reasons=True)
        calls = [call for _ in range(REQUESTS) for call in source.ask(request)]
    finally:
        modelserver.stop()

    latencies = [call.latency_ms for call in calls]
    with capsys.disabled():
        print(
            f"\nlocal:{SMALL} with reasons: {len(calls)} plan choices among {', '.join(open_plans)} at "
            f"{ASKED_AT_SECONDS} s, deadline {request.deadline_ms} ms: median {statistics.median(latencies)} ms, "
            f"max {max(latencies)} ms"
        )
        for call in calls:
            print(f"  {call.latency_ms} ms {call.outcome}:{call.reason} {call.answer} - {call.model_reason}")
    assert len(calls) == REQUESTS
    assert [call.outcome for call in calls] == [USED] * REQUESTS
    assert all(call.answer in open_plans and call.model_reason for call in calls)
    assert statistics.median(latencies) <= REASON_MEDIAN_LIMIT_MS
    assert max(latencies) <= REASON_MAX_LIMIT_MS


@pytest.mark.gpu
def test_briefing_plan_choice_to_small_keeps_the_median_at_most_900_ms_and_the_slowest_at_most_1400_ms(
    run_folder, local_http, capsys
):
    """Twenty briefed plan choices put to the shipped `small` role alone, each with its reason first; prints the
    briefing's length and each answer.

    Every answer has to be used, name an open plan and come with a reason;
    the median latency has to be at most 900 ms and the slowest at most
    1,400 ms, within the plan choice's 1,500 ms deadline.
    """
    request = briefed_plan_choice_at(ASKED_AT_SECONDS)
    open_plans = request.option_ids()
    assert len(open_plans) >= MIN_OPEN_PLANS, f"only {open_plans} open at {ASKED_AT_SECONDS} s"
    write_small_role_only(run_folder)
    served = server_settings.load(run_folder)

    modelserver.start()
    try:
        health = local_http.get(f"{served.endpoint_url}/health").json()
        assert health == {"roles": {SMALL: {"state": "ready"}}}, run_folder / "runs" / "model-server" / f"{SMALL}.log"
        source = LocalModelSource(SMALL)
        calls = [call for _ in range(REQUESTS) for call in source.ask(request)]
    finally:
        modelserver.stop()

    latencies = [call.latency_ms for call in calls]
    with capsys.disabled():
        print(
            f"\nlocal:{SMALL} with the briefing ({len(request.context)} characters) and the reason first: "
            f"{len(calls)} plan choices among {', '.join(open_plans)} at {ASKED_AT_SECONDS} s, deadline "
            f"{request.deadline_ms} ms: median {statistics.median(latencies)} ms, max {max(latencies)} ms"
        )
        for call in calls:
            print(f"  {call.latency_ms} ms {call.outcome}:{call.reason} {call.answer} - {call.model_reason}")
    assert len(calls) == REQUESTS
    assert [call.outcome for call in calls] == [USED] * REQUESTS
    assert all(call.answer in open_plans and call.model_reason for call in calls)
    assert statistics.median(latencies) <= BRIEFING_MEDIAN_LIMIT_MS
    assert max(latencies) <= BRIEFING_MAX_LIMIT_MS


class _ProcessMemoryCounters(ctypes.Structure):
    """psapi.h PROCESS_MEMORY_COUNTERS."""

    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def resident_bytes(pid: int) -> tuple[int, int]:
    """The process's resident memory now and at its peak so far, in bytes: its working set on Windows, RSS elsewhere."""
    if sys.platform == "win32":
        return _working_set(pid)
    status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
    return int(status["VmRSS"].split()[0]) * 1024, int(status["VmHWM"].split()[0]) * 1024


def _working_set(pid: int) -> tuple[int, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        counters = _ProcessMemoryCounters(cb=ctypes.sizeof(_ProcessMemoryCounters))
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)
    return counters.WorkingSetSize, counters.PeakWorkingSetSize


@pytest.fixture
def launched(monkeypatch) -> list[children.Child]:
    """Every llama-server the model server launches during the test: only these are measured, never another one."""
    started = []
    real_spawn = children.spawn

    def spawn_and_remember(arguments, log_path):
        child = real_spawn(arguments, log_path)
        started.append(child)
        return child

    monkeypatch.setattr(children, "spawn", spawn_and_remember)
    return started


def write_shipped_models(root: Path) -> None:
    """Copy the shipped `config/models.toml` into `root` unchanged, so a role starts with every shipped setting."""
    (root / "config").mkdir()
    (root / "config" / "models.toml").write_bytes((REPO_ROOT / "config" / "models.toml").read_bytes())


@pytest.mark.gpu
def test_strong_role_with_no_mmap_stays_under_4_gb_resident_while_it_answers(run_folder, local_http, launched, capsys):
    """The shipped `strong` role alone, as the agent autostarts it: its llama-server stays under 4 GB resident.

    Its resident memory is sampled from the moment it is ready until one completion is answered, which has to
    take less than 30 s. Prints the samples' maximum, the process's peak and the answer's time.
    """
    write_shipped_models(run_folder)
    served = server_settings.load(run_folder)
    (strong,) = [role for role in served.roles if role.name == STRONG]
    assert strong.extra_args == ("--no-mmap",)
    request = {
        "model": STRONG,
        "messages": [{"role": "user", "content": 'Answer with the JSON object {"choice": "HOLD"}.'}],
        "max_tokens": 8,
    }

    modelserver.start(roles=[STRONG])
    try:
        health = local_http.get(f"{served.endpoint_url}/health").json()
        assert health == {"roles": {STRONG: {"state": "ready"}}}, run_folder / "runs" / "model-server" / "strong.log"
        (server,) = launched
        samples = [resident_bytes(server.pid)[0]]
        with ThreadPoolExecutor(max_workers=1) as pool:
            started = time.monotonic()
            answer = pool.submit(
                local_http.post,
                f"{served.endpoint_url}/v1/chat/completions",
                json=request,
                timeout=COMPLETION_LIMIT_SECONDS,
            )
            while not answer.done():
                time.sleep(SAMPLE_SECONDS)
                samples.append(resident_bytes(server.pid)[0])
            response = answer.result()
            seconds = time.monotonic() - started
        now, peak = resident_bytes(server.pid)
        samples.append(now)
    finally:
        modelserver.stop()

    with capsys.disabled():
        print(
            f"\nlocal:{STRONG} with --no-mmap: resident at most {max(samples) / BYTES_PER_GB:.2f} GB over "
            f"{len(samples)} samples from ready to answered, process peak {peak / BYTES_PER_GB:.2f} GB, "
            f"answered HTTP {response.status_code} in {seconds * 1000:.0f} ms"
        )
    assert response.status_code == 200 and seconds < COMPLETION_LIMIT_SECONDS
    assert response.json()["choices"][0]["message"]["content"].strip()
    assert max(samples) < RESIDENT_LIMIT_BYTES
