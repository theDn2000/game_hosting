import logging
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.domain.models import Game, GameServer, Plan, User
from app.errors import (
    DatabaseUnavailableError,
    GameHostingError,
    InvalidDataError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.services.server_registry import RegisteredServer
from app.services.server_service import ServerStatus

TOKEN = "test-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SERVER_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
OTHER_ID = uuid.UUID("66666666-7777-4888-9999-000000000000")
CONTAINER = "secret-container-name"
OWNER_EMAIL = "owner@example.com"


def make_registered(server_id=SERVER_ID, status=ServerStatus.RUNNING, name="My world"):
    owner = User.new(OWNER_EMAIL, created_at=datetime(2026, 11, 4, tzinfo=UTC))
    plan = Plan.new("dev", "Dev", memory_mb=4096, cpu_millicores=2000)
    server = GameServer(
        id=server_id,
        owner_id=owner.id,
        plan_id=plan.id,
        game=Game.MINECRAFT_JAVA,
        name=name,
        container_name=CONTAINER,
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
    )
    return RegisteredServer(server, owner, plan, status)


class FakeRegistry:
    """Stand-in for ServerRegistry that records calls and can raise an error."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def _record(self, *call):
        self.calls.append(call)
        if self.error:
            raise self.error

    def list_servers(self):
        self._record("list")
        return [make_registered(SERVER_ID, name="first"), make_registered(OTHER_ID, ServerStatus.EXITED, "second")]

    def get_server(self, server_id):
        self._record("get", server_id)
        return make_registered(server_id)

    def start_server(self, server_id):
        self._record("start", server_id)
        return make_registered(server_id, ServerStatus.RUNNING)

    def stop_server(self, server_id, timeout):
        self._record("stop", server_id, timeout)
        return make_registered(server_id, ServerStatus.EXITED)

    def restart_server(self, server_id, timeout):
        self._record("restart", server_id, timeout)
        return make_registered(server_id, ServerStatus.RUNNING)

    def get_server_logs(self, server_id, tail):
        self._record("logs", server_id, tail)
        return "line 1\nline 2\n"


def make_client(registry=None, **kwargs):
    registry = registry or FakeRegistry()
    app = create_app(registry, api_token=TOKEN)
    return TestClient(app, headers=AUTH, **kwargs), registry


PATH = f"/servers/{SERVER_ID}"


@pytest.mark.parametrize(
    "method, path, expected_call, expected_status",
    [
        ("get", PATH, ("get", SERVER_ID), "running"),
        ("post", f"{PATH}/start", ("start", SERVER_ID), "running"),
        ("post", f"{PATH}/stop", ("stop", SERVER_ID, 30), "exited"),
        ("post", f"{PATH}/restart", ("restart", SERVER_ID, 30), "running"),
    ],
)
def test_server_endpoints_call_the_registry_with_defaults(
    method, path, expected_call, expected_status
):
    client, registry = make_client()
    response = client.request(method, path)
    assert response.status_code == 200
    assert response.json() == {
        "id": str(SERVER_ID),
        "name": "My world",
        "game": "minecraft-java",
        "status": expected_status,
    }
    assert registry.calls == [expected_call]


def test_list_returns_every_server_in_the_registry_order():
    client, registry = make_client()
    response = client.get("/servers")
    assert response.status_code == 200
    assert [s["id"] for s in response.json()] == [str(SERVER_ID), str(OTHER_ID)]
    assert [s["status"] for s in response.json()] == ["running", "exited"]
    assert registry.calls == [("list",)]


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/servers"),
        ("get", PATH),
        ("post", f"{PATH}/start"),
        ("post", f"{PATH}/stop"),
        ("post", f"{PATH}/restart"),
        ("get", f"{PATH}/logs"),
    ],
)
def test_responses_never_reveal_the_container_name_or_the_owner(method, path):
    client, _ = make_client()
    response = client.request(method, path)
    assert response.status_code == 200
    assert CONTAINER not in response.text
    assert OWNER_EMAIL not in response.text
    if response.headers["content-type"].startswith("application/json"):
        body = response.json()
        for server in body if isinstance(body, list) else [body]:
            assert set(server) == {"id", "name", "game", "status"}


def test_timeout_and_tail_query_parameters_are_passed():
    client, registry = make_client()
    client.post(f"{PATH}/stop?timeout=60")
    client.post(f"{PATH}/restart?timeout=5")
    client.get(f"{PATH}/logs?tail=7")
    assert registry.calls == [
        ("stop", SERVER_ID, 60),
        ("restart", SERVER_ID, 5),
        ("logs", SERVER_ID, 7),
    ]


def test_logs_are_plain_text_with_default_tail():
    client, registry = make_client()
    response = client.get(f"{PATH}/logs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "line 1\nline 2\n"
    assert registry.calls == [("logs", SERVER_ID, 100)]


def test_state_changing_endpoints_reject_get():
    client, registry = make_client()
    for action in ("start", "stop", "restart"):
        assert client.get(f"{PATH}/{action}").status_code == 405
    assert registry.calls == []


@pytest.mark.parametrize(
    "error, status_code, detail",
    [
        (ServerNotFoundError(str(SERVER_ID)), 404, "Server not found."),
        (RuntimeUnavailableError("docker down"), 503, "The server runtime is unavailable."),
        (DatabaseUnavailableError("db down"), 503, "The platform database is unavailable."),
        (ServerOperationError("docker said no"), 500, "The operation failed."),
        (InvalidDataError("stored data is corrupt"), 500, "The operation failed."),
        (GameHostingError("something else"), 500, "The operation failed."),
    ],
)
def test_application_errors_map_to_http_errors(error, status_code, detail):
    client, _ = make_client(FakeRegistry(error=error))
    response = client.post(f"{PATH}/start")
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize(
    "error",
    [
        RuntimeUnavailableError("SECRET C:\\docker\\path"),
        DatabaseUnavailableError("SECRET postgresql://host/db"),
        ServerOperationError("SECRET"),
    ],
    ids=["docker", "database", "operation"],
)
def test_internal_details_are_logged_but_not_returned(caplog, error):
    client, _ = make_client(FakeRegistry(error=error))
    with caplog.at_level(logging.INFO, logger="game_hosting.api"):
        response = client.get(PATH)
    assert "SECRET" not in response.text
    assert any("SECRET" in record.getMessage() for record in caplog.records)
    assert all(record.levelno >= logging.ERROR for record in caplog.records)


def test_not_found_is_logged_at_info(caplog):
    client, _ = make_client(FakeRegistry(error=ServerNotFoundError(str(SERVER_ID))))
    with caplog.at_level(logging.INFO, logger="game_hosting.api"):
        client.post(f"{PATH}/start")
    assert [record.levelno for record in caplog.records] == [logging.INFO]


def test_unexpected_exception_is_a_generic_500():
    client, _ = make_client(
        FakeRegistry(error=RuntimeError("SECRET bug")), raise_server_exceptions=False
    )
    response = client.get(PATH)
    assert response.status_code == 500
    assert "SECRET" not in response.text


@pytest.mark.parametrize(
    "bad_id",
    [
        "minecraft-dev",  # the old container-name addressing is gone
        "not-a-uuid",
        "123",
        "has%20space",
        "semi;colon",
        "1111111-2222-4333-8444-555555555555",  # one digit short
        "11111111-2222-4333-8444-55555555555z",
    ],
)
def test_ids_that_are_not_uuids_are_rejected_before_the_registry(bad_id):
    client, registry = make_client()
    assert client.get(f"/servers/{bad_id}").status_code == 422
    assert client.post(f"/servers/{bad_id}/start").status_code == 422
    assert client.get(f"/servers/{bad_id}/logs").status_code == 422
    assert registry.calls == []


@pytest.mark.parametrize(
    "written",
    [str(SERVER_ID).upper(), SERVER_ID.hex, f"{SERVER_ID}"],
    ids=["uppercase", "no-hyphens", "canonical"],
)
def test_equivalent_uuid_spellings_reach_the_registry_as_the_same_id(written):
    client, registry = make_client()
    assert client.get(f"/servers/{written}").status_code == 200
    assert registry.calls == [("get", SERVER_ID)]


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", f"{PATH}/stop?timeout=0"),
        ("post", f"{PATH}/stop?timeout=301"),
        ("post", f"{PATH}/restart?timeout=abc"),
        ("get", f"{PATH}/logs?tail=0"),
        ("get", f"{PATH}/logs?tail=1001"),
        ("get", f"{PATH}/logs?tail=abc"),
    ],
)
def test_invalid_query_parameters_are_rejected_before_the_registry(method, path):
    client, registry = make_client()
    assert client.request(method, path).status_code == 422
    assert registry.calls == []


@pytest.mark.parametrize(
    "method, path, expected_call",
    [
        ("post", f"{PATH}/stop?timeout=300", ("stop", SERVER_ID, 300)),
        ("post", f"{PATH}/stop?timeout=1", ("stop", SERVER_ID, 1)),
        ("get", f"{PATH}/logs?tail=1000", ("logs", SERVER_ID, 1000)),
        ("get", f"{PATH}/logs?tail=1", ("logs", SERVER_ID, 1)),
    ],
)
def test_query_parameter_boundaries_are_accepted(method, path, expected_call):
    client, registry = make_client()
    assert client.request(method, path).status_code == 200
    assert registry.calls == [expected_call]
