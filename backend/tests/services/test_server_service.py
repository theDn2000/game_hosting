import pytest

from app.errors import (
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.services.server_service import ServerService, ServerStatus

NAME = "minecraft-dev"


class FakeDockerClient:
    """In-memory stand-in for DockerClient that records the calls it receives."""

    def __init__(self, statuses=None, error=None):
        self.statuses = dict(statuses or {})
        self.error = error
        self.calls = []

    def _status(self, name):
        if self.error:
            raise self.error
        if name not in self.statuses:
            raise ServerNotFoundError(name)
        return self.statuses[name]

    def get_status(self, name):
        return self._status(name)

    def start_container(self, name):
        self._status(name)
        self.calls.append(("start", name))
        self.statuses[name] = "running"

    def stop_container(self, name, timeout):
        self._status(name)
        self.calls.append(("stop", name, timeout))
        self.statuses[name] = "exited"

    def restart_container(self, name, timeout):
        self._status(name)
        self.calls.append(("restart", name, timeout))
        self.statuses[name] = "running"

    def get_logs(self, name, tail):
        self._status(name)
        self.calls.append(("logs", name, tail))
        return "log line\n"


def make_service(**kwargs):
    fake = FakeDockerClient(**kwargs)
    return ServerService(docker_client=fake), fake


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("running", ServerStatus.RUNNING),
        ("exited", ServerStatus.EXITED),
        ("created", ServerStatus.CREATED),
        ("paused", ServerStatus.PAUSED),
        ("restarting", ServerStatus.RESTARTING),
        ("removing", ServerStatus.REMOVING),
        ("dead", ServerStatus.DEAD),
        ("something-new", ServerStatus.UNKNOWN),
    ],
)
def test_status_maps_docker_status(raw, expected):
    service, _ = make_service(statuses={NAME: raw})
    assert service.get_server_status(NAME) == expected


def test_status_of_missing_server_is_not_found_value():
    service, _ = make_service()
    assert service.get_server_status(NAME) == ServerStatus.NOT_FOUND


def test_status_does_not_hide_runtime_errors():
    service, _ = make_service(error=RuntimeUnavailableError("docker is down"))
    with pytest.raises(RuntimeUnavailableError):
        service.get_server_status(NAME)


def test_start_stopped_server():
    service, docker = make_service(statuses={NAME: "exited"})
    assert service.start_server(NAME) == ServerStatus.RUNNING
    assert docker.calls == [("start", NAME)]


def test_start_is_idempotent_when_already_running():
    service, docker = make_service(statuses={NAME: "running"})
    assert service.start_server(NAME) == ServerStatus.RUNNING
    assert docker.calls == []


def test_stop_running_server_uses_default_timeout():
    service, docker = make_service(statuses={NAME: "running"})
    assert service.stop_server(NAME) == ServerStatus.EXITED
    assert docker.calls == [("stop", NAME, 30)]


def test_stop_passes_custom_timeout():
    service, docker = make_service(statuses={NAME: "running"})
    service.stop_server(NAME, timeout=60)
    assert docker.calls == [("stop", NAME, 60)]


@pytest.mark.parametrize("raw", ["exited", "created", "dead"])
def test_stop_is_idempotent_when_not_running(raw):
    service, docker = make_service(statuses={NAME: raw})
    assert service.stop_server(NAME) == ServerStatus(raw)
    assert docker.calls == []


def test_restart_uses_default_timeout():
    service, docker = make_service(statuses={NAME: "running"})
    assert service.restart_server(NAME) == ServerStatus.RUNNING
    assert docker.calls == [("restart", NAME, 30)]


def test_restart_starts_a_stopped_server_with_custom_timeout():
    service, docker = make_service(statuses={NAME: "exited"})
    assert service.restart_server(NAME, timeout=10) == ServerStatus.RUNNING
    assert docker.calls == [("restart", NAME, 10)]


def test_logs_use_default_tail():
    service, docker = make_service(statuses={NAME: "running"})
    assert service.get_server_logs(NAME) == "log line\n"
    assert docker.calls == [("logs", NAME, 100)]


def test_logs_pass_custom_tail():
    service, docker = make_service(statuses={NAME: "running"})
    service.get_server_logs(NAME, tail=5)
    assert docker.calls == [("logs", NAME, 5)]


@pytest.mark.parametrize("tail", [0, -1])
def test_logs_reject_non_positive_tail_without_calling_docker(tail):
    service, docker = make_service(statuses={NAME: "running"})
    with pytest.raises(ValueError):
        service.get_server_logs(NAME, tail=tail)
    assert docker.calls == []


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.start_server("ghost"),
        lambda s: s.stop_server("ghost"),
        lambda s: s.restart_server("ghost"),
        lambda s: s.get_server_logs("ghost"),
    ],
    ids=["start", "stop", "restart", "logs"],
)
def test_operations_on_missing_server_raise_not_found(call):
    service, docker = make_service()
    with pytest.raises(ServerNotFoundError):
        call(service)
    assert docker.calls == []


@pytest.mark.parametrize(
    "error", [RuntimeUnavailableError("down"), ServerOperationError("failed")]
)
def test_infrastructure_errors_propagate_from_operations(error):
    service, _ = make_service(error=error)
    with pytest.raises(type(error)):
        service.start_server(NAME)
