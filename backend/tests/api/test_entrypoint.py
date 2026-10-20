import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.entrypoint import (
    DOCS_VARIABLE,
    TOKEN_VARIABLE,
    ConfigurationError,
    build_app,
)

TOKEN = "env-token-0123456789abcdef"


@pytest.mark.parametrize(
    "environ",
    [{}, {TOKEN_VARIABLE: ""}, {TOKEN_VARIABLE: "short"}, {DOCS_VARIABLE: "1"}],
    ids=["unset", "empty", "short", "only-docs-flag"],
)
def test_build_app_fails_closed_without_a_valid_token(environ):
    with pytest.raises(ConfigurationError) as excinfo:
        build_app(environ)
    assert TOKEN_VARIABLE in str(excinfo.value)
    assert "secrets.token_urlsafe" in str(excinfo.value)


def test_build_app_does_not_echo_a_rejected_token_in_the_error():
    with pytest.raises(ConfigurationError) as excinfo:
        build_app({TOKEN_VARIABLE: "leaky-1234"})
    assert "leaky-1234" not in str(excinfo.value)


def test_build_app_with_token_needs_no_docker_and_protects_routes():
    app = build_app({TOKEN_VARIABLE: TOKEN})
    assert isinstance(app, FastAPI)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/servers/minecraft-dev").status_code == 401
    assert client.get("/docs").status_code == 404


@pytest.mark.parametrize("value, enabled", [("1", True), ("true", True), ("TRUE", True), ("0", False), ("false", False), ("", False), ("yes", False)])
def test_docs_flag_parsing(value, enabled):
    app = build_app({TOKEN_VARIABLE: TOKEN, DOCS_VARIABLE: value})
    assert (TestClient(app).get("/docs").status_code == 200) is enabled
