"""The courtesy memory lease with ModelBouncer, with the network replaced by a stub transport."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from ucla_agent.core.modelserver import courtesy

MIB = 1024 * 1024
WARNING_START = "Warning: ModelBouncer did not take the courtesy memory lease: "
WARNING_END = ". The model server starts all the same.\n"


class _ModelBouncer:
    """Stands in for the service: remembers every request and answers each route as the test says."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status_answer: httpx.Response | Exception = httpx.Response(200, json={"state": "ready"})
        self.lease_answer: httpx.Response | Exception = httpx.Response(201, json={"lease_id": "lease-1"})
        self.transport = httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.status_answer if request.url.path == "/status" else self.lease_answer
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def service() -> _ModelBouncer:
    return _ModelBouncer()


@pytest.fixture
def model_files(run_folder: Path) -> list[Path]:
    """Two model files of 3 MiB and 1.5 MiB, and the path of one that was never downloaded."""
    first, second = run_folder / "fast.gguf", run_folder / "strong.gguf"
    first.write_bytes(b"\0" * (3 * MIB))
    second.write_bytes(b"\0" * (MIB + MIB // 2))
    return [first, second, run_folder / "not-downloaded.gguf"]


# -- the lease that is asked for -------------------------------------------------------------------


def test_the_lease_is_the_model_files_that_exist_rounded_up_plus_4096_mib(model_files):
    assert courtesy.lease_mib(model_files) == 5 + 4096


def test_no_model_file_at_all_still_asks_for_the_margin(run_folder):
    assert courtesy.lease_mib([run_folder / "not-downloaded.gguf"]) == 4096


def test_the_service_is_the_one_on_port_11435_of_this_machine():
    assert courtesy.MODELBOUNCER_URL == "http://127.0.0.1:11435"
    assert courtesy.STATUS_TIMEOUT_SECONDS == 1.0


def test_a_service_that_answers_its_status_gets_one_memory_lease_owned_by_this_process(service, model_files, capsys):
    courtesy.request_lease(model_files, transport=service.transport)

    status_request, lease_request = service.requests
    assert (status_request.method, str(status_request.url)) == ("GET", "http://127.0.0.1:11435/status")
    assert (lease_request.method, str(lease_request.url)) == ("POST", "http://127.0.0.1:11435/v1/leases")
    assert json.loads(lease_request.content) == {
        "kind": "memory",
        "client": "ucla-agent",
        "vram_mb": 4101,
        "exclusive": False,
        "owner_pid": os.getpid(),
    }
    assert lease_request.headers["x-modelbouncer-client"] == "ucla-agent"
    assert capsys.readouterr() == ("", "")


def test_the_status_question_waits_at_most_one_second(service, model_files):
    courtesy.request_lease(model_files, transport=service.transport)

    assert set(service.requests[0].extensions["timeout"].values()) == {1.0}


def test_a_queued_lease_is_not_a_failure(service, model_files, capsys):
    service.lease_answer = httpx.Response(202, json={"lease_id": "lease-1", "position": 1})

    courtesy.request_lease(model_files, transport=service.transport)

    assert capsys.readouterr() == ("", "")


# -- no service, or a service that does not take the lease -----------------------------------------


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("connection refused"), httpx.ConnectTimeout("no answer"), httpx.ReadTimeout("no answer")],
    ids=["refused", "connect timeout", "read timeout"],
)
def test_without_an_answer_to_the_status_question_nothing_is_sent_or_printed(service, model_files, capsys, failure):
    service.status_answer = failure

    courtesy.request_lease(model_files, transport=service.transport)

    assert [request.url.path for request in service.requests] == ["/status"]
    assert capsys.readouterr() == ("", "")


def test_a_service_that_answers_its_status_with_an_error_is_still_asked(service, model_files):
    service.status_answer = httpx.Response(503, json={"error": "reconciling"})

    courtesy.request_lease(model_files, transport=service.transport)

    assert [request.url.path for request in service.requests] == ["/status", "/v1/leases"]


@pytest.mark.parametrize("status", [507, 503, 422, 200])
def test_a_refused_lease_is_one_warning_line_and_nothing_more(service, model_files, capsys, status):
    service.lease_answer = httpx.Response(status, json={"detail": {"error": "never_fits"}})

    courtesy.request_lease(model_files, transport=service.transport)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{WARNING_START}it answered HTTP {status}{WARNING_END}"


@pytest.mark.parametrize(
    "failure, name",
    [(httpx.ReadTimeout("no answer"), "ReadTimeout"), (httpx.ConnectError("gone"), "ConnectError")],
    ids=["timeout", "connection lost"],
)
def test_a_lease_request_that_fails_is_one_warning_line_and_nothing_more(service, model_files, capsys, failure, name):
    service.lease_answer = failure

    courtesy.request_lease(model_files, transport=service.transport)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{WARNING_START}the request failed ({name}){WARNING_END}"


# -- a lease step that fails before anything is sent -----------------------------------------------


class _VanishedFile:
    """Stands in for a model file that is removed between being found and being measured."""

    def is_file(self) -> bool:
        return True

    def stat(self) -> None:
        raise FileNotFoundError("the model file was removed")


def test_the_size_of_a_file_that_cannot_be_measured_is_an_error():
    with pytest.raises(OSError):
        courtesy.lease_mib([_VanishedFile()])


def test_a_model_file_that_cannot_be_measured_is_one_warning_line_and_no_lease(service, run_folder, capsys):
    courtesy.request_lease([_VanishedFile()], transport=service.transport)

    captured = capsys.readouterr()
    assert [request.url.path for request in service.requests] == ["/status"]
    assert captured.out == ""
    assert captured.err == f"{WARNING_START}the lease could not be asked for (FileNotFoundError){WARNING_END}"


def test_any_other_failure_of_the_lease_step_is_one_warning_line_and_nothing_more(
    service, model_files, capsys, monkeypatch
):
    def fail(_model_paths) -> int:
        raise RuntimeError("something unforeseen")

    monkeypatch.setattr(courtesy, "lease_mib", fail)

    courtesy.request_lease(model_files, transport=service.transport)

    assert capsys.readouterr().err == f"{WARNING_START}the lease could not be asked for (RuntimeError){WARNING_END}"
