import logging

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.errors import (
    GameHostingError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.services.server_service import ServerStatus

NAME = "minecraft-dev"
TOKEN = "test-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeService:
    """Stand-in for ServerService that records calls and can raise an error."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def _record(self, *call):
        self.calls.append(call)
        if self.error:
            raise self.error

    def get_server_status(self, name):
        self._record("status", name)
        return ServerStatus.RUNNING

    def start_server(self, name):
        self._record("start", name)
        return ServerStatus.RUNNING

    def stop_server(self, name, timeout):
        self._record("stop", name, timeout)
        return ServerStatus.EXITED

    def restart_server(self, name, timeout):
        self._record("restart", name, timeout)
        return ServerStatus.RUNNING

    def get_server_logs(self, name, tail):
        self._record("logs", name, tail)
        return "line 1\nline 2\n"


def make_client(service=None, **kwargs):
    service = service or FakeService()
    app = create_app(service, api_token=TOKEN)
    return TestClient(app, headers=AUTH, **kwargs), service


@pytest.mark.parametrize(
    "method, path, expected_call, expected_status",
    [
        ("get", f"/servers/{NAME}", ("status", NAME), "running"),
        ("post", f"/servers/{NAME}/start", ("start", NAME), "running"),
        ("post", f"/servers/{NAME}/stop", ("stop", NAME, 30), "exited"),
        ("post", f"/servers/{NAME}/restart", ("restart", NAME, 30), "running"),
    ],
)
def test_server_endpoints_call_service_with_defaults(
    method, path, expected_call, expected_status
):
    client, service = make_client()
    response = getattr(client, method)(path)
    assert response.status_code == 200
    assert response.json() == {"server": NAME, "status": expected_status}
    assert service.calls == [expected_call]


def test_timeout_and_tail_query_parameters_are_passed():
    client, service = make_client()
    client.post(f"/servers/{NAME}/stop?timeout=60")
    client.post(f"/servers/{NAME}/restart?timeout=5")
    client.get(f"/servers/{NAME}/logs?tail=7")
    assert service.calls == [
        ("stop", NAME, 60),
        ("restart", NAME, 5),
        ("logs", NAME, 7),
    ]


def test_logs_are_plain_text_with_default_tail():
    client, service = make_client()
    response = client.get(f"/servers/{NAME}/logs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "line 1\nline 2\n"
    assert service.calls == [("logs", NAME, 100)]


def test_state_changing_endpoints_reject_get():
    client, service = make_client()
    for action in ("start", "stop", "restart"):
        assert client.get(f"/servers/{NAME}/{action}").status_code == 405
    assert service.calls == []


@pytest.mark.parametrize(
    "error, status_code, detail",
    [
        (ServerNotFoundError(NAME), 404, "Server not found."),
        (RuntimeUnavailableError("docker down"), 503, "The server runtime is unavailable."),
        (ServerOperationError("docker said no"), 500, "The operation failed."),
        (GameHostingError("something else"), 500, "The operation failed."),
    ],
)
def test_application_errors_map_to_http_errors(error, status_code, detail):
    client, _ = make_client(FakeService(error=error))
    response = client.post(f"/servers/{NAME}/start")
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize(
    "error", [RuntimeUnavailableError("SECRET C:\\docker\\path"), ServerOperationError("SECRET")]
)
def test_internal_details_are_logged_but_not_returned(caplog, error):
    client, _ = make_client(FakeService(error=error))
    with caplog.at_level(logging.INFO, logger="game_hosting.api"):
        response = client.get(f"/servers/{NAME}")
    assert "SECRET" not in response.text
    assert any("SECRET" in record.getMessage() for record in caplog.records)
    assert all(record.levelno >= logging.ERROR for record in caplog.records)


def test_not_found_is_logged_at_info(caplog):
    client, _ = make_client(FakeService(error=ServerNotFoundError(NAME)))
    with caplog.at_level(logging.INFO, logger="game_hosting.api"):
        client.post(f"/servers/{NAME}/start")
    assert [record.levelno for record in caplog.records] == [logging.INFO]


def test_unexpected_exception_is_a_generic_500():
    client, _ = make_client(
        FakeService(error=RuntimeError("SECRET bug")), raise_server_exceptions=False
    )
    response = client.get(f"/servers/{NAME}")
    assert response.status_code == 500
    assert "SECRET" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/servers/-starts-with-dash",
        "/servers/.hidden",
        "/servers/has%20space",
        "/servers/semi;colon",
        "/servers/" + "a" * 65,
    ],
)
def test_invalid_server_names_are_rejected_before_the_service(path):
    client, service = make_client()
    assert client.get(path).status_code == 422
    assert client.post(path + "/start").status_code == 422
    assert service.calls == []


def test_longest_valid_name_is_accepted():
    client, service = make_client()
    name = "a" * 64
    assert client.get(f"/servers/{name}").status_code == 200
    assert service.calls == [("status", name)]


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", f"/servers/{NAME}/stop?timeout=0"),
        ("post", f"/servers/{NAME}/stop?timeout=301"),
        ("post", f"/servers/{NAME}/restart?timeout=abc"),
        ("get", f"/servers/{NAME}/logs?tail=0"),
        ("get", f"/servers/{NAME}/logs?tail=1001"),
        ("get", f"/servers/{NAME}/logs?tail=abc"),
    ],
)
def test_invalid_query_parameters_are_rejected_before_the_service(method, path):
    client, service = make_client()
    assert getattr(client, method)(path).status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "method, path, expected_call",
    [
        ("post", f"/servers/{NAME}/stop?timeout=300", ("stop", NAME, 300)),
        ("post", f"/servers/{NAME}/stop?timeout=1", ("stop", NAME, 1)),
        ("get", f"/servers/{NAME}/logs?tail=1000", ("logs", NAME, 1000)),
        ("get", f"/servers/{NAME}/logs?tail=1", ("logs", NAME, 1)),
    ],
)
def test_query_parameter_boundaries_are_accepted(method, path, expected_call):
    client, service = make_client()
    assert getattr(client, method)(path).status_code == 200
    assert service.calls == [expected_call]
