import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.app import MIN_TOKEN_LENGTH, create_app, require_token
from tests.api.test_servers import SERVER_ID, TOKEN, FakeRegistry

PROTECTED = [
    ("get", "/servers"),
    ("get", f"/servers/{SERVER_ID}"),
    ("post", f"/servers/{SERVER_ID}/start"),
    ("post", f"/servers/{SERVER_ID}/stop"),
    ("post", f"/servers/{SERVER_ID}/restart"),
    ("get", f"/servers/{SERVER_ID}/logs"),
]


def anonymous_client(registry=None, **app_kwargs):
    registry = registry or FakeRegistry()
    app = create_app(registry, api_token=TOKEN, **app_kwargs)
    return TestClient(app), registry


@pytest.mark.parametrize("method, path", PROTECTED)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token-0123456789abcdef"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": "Bearer "},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Bearer {TOKEN[:-1]}"},
    ],
    ids=["none", "wrong", "basic-scheme", "no-scheme", "empty", "longer", "shorter"],
)
def test_protected_routes_reject_missing_or_wrong_token(method, path, headers):
    client, service = anonymous_client()
    response = getattr(client, method)(path, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing token."}
    assert response.headers["www-authenticate"] == "Bearer"
    assert service.calls == []


@pytest.mark.parametrize("method, path", PROTECTED)
def test_protected_routes_accept_the_token(method, path):
    client, service = anonymous_client()
    response = getattr(client, method)(path, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert len(service.calls) == 1


def test_health_is_open():
    client, _ = anonymous_client()
    assert client.get("/health").status_code == 200


def test_authentication_comes_before_validation():
    client, service = anonymous_client()
    assert client.get("/servers/not-a-uuid").status_code == 401
    assert client.post(f"/servers/{SERVER_ID}/stop?timeout=0").status_code == 401
    assert service.calls == []


def test_rejected_requests_are_logged_without_the_token(caplog):
    client, _ = anonymous_client()
    sent = "Bearer wrong-token-0123456789abcdef"
    with caplog.at_level(logging.WARNING, logger="game_hosting.api"):
        client.get(f"/servers/{SERVER_ID}", headers={"Authorization": sent})
    assert len(caplog.records) == 1
    assert "wrong-token" not in caplog.records[0].getMessage()


def test_rejected_request_log_escapes_control_characters_in_the_path(caplog):
    # Built by hand: servers decode percent-escapes in the path, so attackers
    # can plant control characters (Starlette's URL already drops \n \r \t, but
    # not others such as the ANSI escape \x1b).
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/servers/x\x1b[2JWARNING forged line",
            "headers": [],
        }
    )
    verify = require_token(TOKEN)
    with caplog.at_level(logging.WARNING, logger="game_hosting.api"):
        with pytest.raises(HTTPException) as excinfo:
            verify(request, None)
    assert excinfo.value.status_code == 401
    message = caplog.records[0].getMessage()
    assert not any(ord(char) < 32 for char in message)
    assert "forged line" in message  # still visible, but escaped


@pytest.mark.parametrize("token", [None, "", "short", "x" * (MIN_TOKEN_LENGTH - 1)])
def test_create_app_refuses_a_missing_or_short_token(token):
    with pytest.raises(ValueError):
        create_app(FakeRegistry(), api_token=token)


def test_create_app_accepts_the_minimum_token_length():
    create_app(FakeRegistry(), api_token="x" * MIN_TOKEN_LENGTH)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_disabled_by_default(path):
    client, _ = anonymous_client()
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_can_be_enabled(path):
    client, _ = anonymous_client(enable_docs=True)
    assert client.get(path).status_code == 200


def test_enabling_docs_does_not_unprotect_the_routes():
    client, service = anonymous_client(enable_docs=True)
    assert client.post(f"/servers/{SERVER_ID}/stop").status_code == 401
    assert service.calls == []
