from unittest.mock import MagicMock, patch

import pytest
from docker.errors import APIError, DockerException, NotFound
from requests.exceptions import ConnectionError as RequestsConnectionError

from app.errors import (
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.infrastructure.docker_client import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    DockerClient,
)


@pytest.fixture
def sdk():
    """A fake Docker SDK client whose containers.get returns a fake container."""
    sdk = MagicMock()
    sdk.container = sdk.containers.get.return_value
    sdk.container.labels = {MANAGED_LABEL: MANAGED_LABEL_VALUE}
    return sdk


@pytest.fixture
def client(sdk):
    return DockerClient(client=sdk)


def test_constructing_does_not_connect_to_docker():
    with patch("app.infrastructure.docker_client.docker.from_env") as from_env:
        DockerClient()
    from_env.assert_not_called()


def test_connects_lazily_on_first_use(sdk):
    sdk.container.status = "running"
    with patch(
        "app.infrastructure.docker_client.docker.from_env", return_value=sdk
    ) as from_env:
        client = DockerClient()
        assert client.get_status("minecraft-dev") == "running"
        client.get_status("minecraft-dev")
    from_env.assert_called_once()


def test_get_status_returns_docker_status(client, sdk):
    sdk.container.status = "exited"
    assert client.get_status("minecraft-dev") == "exited"
    sdk.containers.get.assert_called_once_with("minecraft-dev")


def test_docker_not_running_is_runtime_unavailable():
    with patch(
        "app.infrastructure.docker_client.docker.from_env",
        side_effect=DockerException("daemon is not running"),
    ):
        with pytest.raises(RuntimeUnavailableError):
            DockerClient().get_status("minecraft-dev")


def test_connection_lost_during_lookup_is_runtime_unavailable(client, sdk):
    sdk.containers.get.side_effect = RequestsConnectionError("connection refused")
    with pytest.raises(RuntimeUnavailableError):
        client.get_status("minecraft-dev")


def test_api_error_during_lookup_is_operation_error(client, sdk):
    sdk.containers.get.side_effect = APIError("internal server error")
    with pytest.raises(ServerOperationError):
        client.get_status("minecraft-dev")


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.get_status("ghost"),
        lambda c: c.start_container("ghost"),
        lambda c: c.stop_container("ghost", timeout=30),
        lambda c: c.restart_container("ghost", timeout=30),
        lambda c: c.get_logs("ghost", tail=10),
    ],
    ids=["status", "start", "stop", "restart", "logs"],
)
def test_missing_container_raises_server_not_found(client, sdk, call):
    sdk.containers.get.side_effect = NotFound("no such container")
    with pytest.raises(ServerNotFoundError) as excinfo:
        call(client)
    assert excinfo.value.server_name == "ghost"


def test_start_container(client, sdk):
    client.start_container("minecraft-dev")
    sdk.container.start.assert_called_once_with()


def test_stop_container_passes_timeout(client, sdk):
    client.stop_container("minecraft-dev", timeout=45)
    sdk.container.stop.assert_called_once_with(timeout=45)


def test_restart_container_passes_timeout(client, sdk):
    client.restart_container("minecraft-dev", timeout=45)
    sdk.container.restart.assert_called_once_with(timeout=45)


@pytest.mark.parametrize("method", ["start", "stop", "restart"])
def test_api_error_during_operation_is_operation_error(client, sdk, method):
    getattr(sdk.container, method).side_effect = APIError("conflict")
    calls = {
        "start": lambda: client.start_container("minecraft-dev"),
        "stop": lambda: client.stop_container("minecraft-dev", timeout=30),
        "restart": lambda: client.restart_container("minecraft-dev", timeout=30),
    }
    with pytest.raises(ServerOperationError):
        calls[method]()


def test_connection_lost_during_operation_is_runtime_unavailable(client, sdk):
    sdk.container.start.side_effect = RequestsConnectionError("connection reset")
    with pytest.raises(RuntimeUnavailableError):
        client.start_container("minecraft-dev")


def test_get_logs_decodes_and_passes_tail(client, sdk):
    sdk.container.logs.return_value = "línea uno\nline two\n".encode("utf-8")
    assert client.get_logs("minecraft-dev", tail=50) == "línea uno\nline two\n"
    sdk.container.logs.assert_called_once_with(tail=50)


def test_get_logs_replaces_undecodable_bytes(client, sdk):
    sdk.container.logs.return_value = b"ok \xff\xfe end"
    logs = client.get_logs("minecraft-dev", tail=10)
    assert logs.startswith("ok ") and logs.endswith(" end")


ALL_OPERATIONS = [
    lambda c: c.get_status("other"),
    lambda c: c.start_container("other"),
    lambda c: c.stop_container("other", timeout=30),
    lambda c: c.restart_container("other", timeout=30),
    lambda c: c.get_logs("other", tail=10),
]
ALL_OPERATION_IDS = ["status", "start", "stop", "restart", "logs"]


@pytest.mark.parametrize(
    "labels",
    [{}, {"unrelated": "x"}, {MANAGED_LABEL: "false"}, {MANAGED_LABEL: ""}],
    ids=["no-labels", "other-label", "wrong-value", "empty-value"],
)
@pytest.mark.parametrize("call", ALL_OPERATIONS, ids=ALL_OPERATION_IDS)
def test_unmanaged_container_is_reported_as_not_found(client, sdk, call, labels):
    sdk.container.labels = labels
    with pytest.raises(ServerNotFoundError) as excinfo:
        call(client)
    assert excinfo.value.server_name == "other"
    for method in ("start", "stop", "restart", "logs"):
        getattr(sdk.container, method).assert_not_called()


def test_managed_container_is_accepted(client, sdk):
    sdk.container.labels = {MANAGED_LABEL: MANAGED_LABEL_VALUE, "other": "x"}
    sdk.container.status = "running"
    assert client.get_status("minecraft-dev") == "running"
