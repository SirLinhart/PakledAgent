"""The socket guard every test runs under: no test touches a port the live agent's model server holds.

The guarded calls are made on UDP sockets: a UDP connect sends nothing and a
UDP port is not the TCP port a model server listens on, so these tests could
not reach or disturb a running model server even if the guard let them through.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = "127.0.0.1"
SHIPPED_PORTS = (11500, 41500, 41501, 41502, 41503)
GUARD_MESSAGE = "a port of the shipped model server that the live agent may hold"
# A test file run in a child pytest under this repository's conftest.py: the same contact, once in a test marked gpu.
GPU_EXEMPTION_TESTS = """
import socket

import pytest


def _connect_to_the_strong_backend_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("127.0.0.1", 41501))


@pytest.mark.gpu
def test_marked_gpu():
    _connect_to_the_strong_backend_port()


def test_not_marked():
    _connect_to_the_strong_backend_port()
"""
CHILD_PYTEST_SETTINGS = "[pytest]\nmarkers =\n    gpu: starts the real llama-server\n"


def _udp_socket() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _local_port(sock: socket.socket) -> int:
    """The port a socket is bound to; 0 when it is bound to none."""
    try:
        return sock.getsockname()[1]
    except OSError:  # Windows gives a socket that was never bound no name at all
        return 0


def _is_connected(sock: socket.socket) -> bool:
    try:
        sock.getpeername()
    except OSError:
        return False
    return True


@pytest.mark.parametrize("port", SHIPPED_PORTS)
@pytest.mark.parametrize("action", ["connect", "connect_ex"])
def test_a_connect_to_a_shipped_model_port_fails_the_test_before_the_socket_is_used(action, port):
    with _udp_socket() as sock:
        with pytest.raises(pytest.fail.Exception, match=f"tried to {action} 127.0.0.1:{port}, {GUARD_MESSAGE}"):
            getattr(sock, action)((LOOPBACK, port))

        assert not _is_connected(sock)


@pytest.mark.parametrize("port", SHIPPED_PORTS)
def test_a_bind_to_a_shipped_model_port_fails_the_test_before_the_port_is_taken(port):
    with _udp_socket() as sock:
        with pytest.raises(pytest.fail.Exception, match=f"tried to bind 127.0.0.1:{port}, {GUARD_MESSAGE}"):
            sock.bind((LOOPBACK, port))

        assert _local_port(sock) == 0


@pytest.mark.parametrize("host", ["localhost", "127.0.0.2", "0.0.0.0", "", "::1", "::"])
def test_every_way_of_naming_this_machine_is_guarded(shipped_model_ports_guard, host):
    with pytest.raises(pytest.fail.Exception, match=GUARD_MESSAGE):
        shipped_model_ports_guard.check("connect", (host, 11500))


@pytest.mark.parametrize(
    "address",
    [("192.0.2.1", 11500), ("example.invalid", 41501), (LOOPBACK, 41504), (LOOPBACK, 41499), "/a/unix/socket"],
    ids=["another machine", "a host name", "the port after jev9b", "the port before fast", "not an internet address"],
)
def test_other_addresses_pass(shipped_model_ports_guard, address):
    assert shipped_model_ports_guard.check("connect", address) is None


def test_sockets_on_other_local_ports_work_as_before():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((LOOPBACK, 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection((LOOPBACK, port)) as client:
            assert client.getpeername() == (LOOPBACK, port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
            assert other.connect_ex((LOOPBACK, port)) == 0


def test_a_contact_on_the_test_s_own_thread_fails_there_and_is_not_kept_for_teardown(shipped_model_ports_guard):
    with _udp_socket() as sock, pytest.raises(pytest.fail.Exception):
        sock.connect((LOOPBACK, 11500))

    assert shipped_model_ports_guard.off_thread_contacts == []


def test_a_contact_from_another_thread_is_kept_and_fails_the_test_at_teardown(shipped_model_ports_guard):
    raised = []

    def contact() -> None:
        with _udp_socket() as sock:
            try:
                sock.connect((LOOPBACK, 41501))
            except BaseException as exc:  # pytest's failure is a BaseException; kept to be looked at below
                raised.append(exc)

    thread = threading.Thread(target=contact)
    thread.start()
    thread.join()

    assert [type(exc) for exc in raised] == [pytest.fail.Exception]
    assert shipped_model_ports_guard.off_thread_contacts == ["connect 127.0.0.1:41501"]
    with pytest.raises(pytest.fail.Exception, match="a thread of the test touched a shipped model port: connect"):
        shipped_model_ports_guard.check_no_off_thread_contact()
    # The contact above is this test's subject, not a fault of the test.
    shipped_model_ports_guard.off_thread_contacts.clear()


def test_a_test_marked_gpu_is_not_guarded_and_an_unmarked_one_is(tmp_path):
    (tmp_path / "test_contacts.py").write_text(GPU_EXEMPTION_TESTS, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(CHILD_PYTEST_SETTINGS, encoding="utf-8")
    # The repository's conftest.py is loaded as a plugin, so its guard runs over a test file outside the repository.
    search_path = os.pathsep.join([str(REPO_ROOT / "tests"), str(REPO_ROOT / "src")])
    environment = {**os.environ, "PYTHONPATH": search_path}

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "conftest", "-p", "no:cacheprovider", "-rA", "test_contacts.py"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "PASSED test_contacts.py::test_marked_gpu" in completed.stdout
    assert "FAILED test_contacts.py::test_not_marked" in completed.stdout
    assert f"the test tried to connect 127.0.0.1:41501, {GUARD_MESSAGE}" in completed.stdout
