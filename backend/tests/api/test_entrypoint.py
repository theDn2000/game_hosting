import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.entrypoint import (
    DOCS_VARIABLE,
    LOG_LEVEL_VARIABLE,
    TOKEN_VARIABLE,
    ConfigurationError,
    build_app,
    configure_logging,
)
from app.infrastructure.db.database import DATABASE_URL_VARIABLE

TOKEN = "env-token-0123456789abcdef"
DB_PASSWORD = "db-s3cret-p4ss"
# Nothing listens on port 1; building the app must not try to connect anyway.
DB_URL = f"postgresql+psycopg://gh_user:{DB_PASSWORD}@127.0.0.1:1/gh_db"
ENV = {TOKEN_VARIABLE: TOKEN, DATABASE_URL_VARIABLE: DB_URL}
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def clean_game_hosting_logger():
    logger = logging.getLogger("game_hosting")
    saved_handlers, saved_level = list(logger.handlers), logger.level
    yield logger
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


# --- token ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "token_env",
    [{}, {TOKEN_VARIABLE: ""}, {TOKEN_VARIABLE: "short"}],
    ids=["unset", "empty", "short"],
)
def test_build_app_fails_closed_without_a_valid_token(token_env):
    with pytest.raises(ConfigurationError) as excinfo:
        build_app({DATABASE_URL_VARIABLE: DB_URL, **token_env})
    assert TOKEN_VARIABLE in str(excinfo.value)
    assert "secrets.token_urlsafe" in str(excinfo.value)


def test_build_app_does_not_echo_a_rejected_token_in_the_error():
    with pytest.raises(ConfigurationError) as excinfo:
        build_app({DATABASE_URL_VARIABLE: DB_URL, TOKEN_VARIABLE: "leaky-1234"})
    assert "leaky-1234" not in str(excinfo.value)


# --- database URL -----------------------------------------------------------------


@pytest.mark.parametrize("url_env", [{}, {DATABASE_URL_VARIABLE: ""}], ids=["unset", "empty"])
def test_build_app_fails_closed_without_a_database_url(url_env):
    with pytest.raises(ConfigurationError) as excinfo:
        build_app({TOKEN_VARIABLE: TOKEN, **url_env})
    assert DATABASE_URL_VARIABLE in str(excinfo.value)
    assert "must be set" in str(excinfo.value)  # not merely "not usable"


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://u:{DB_PASSWORD}@h/db",
        f"sqlite:///{DB_PASSWORD}.db",
        f"mysql+pymysql://u:{DB_PASSWORD}@h/db",
        f"user:{DB_PASSWORD}@host-without-scheme/db",
    ],
    ids=["no-driver", "sqlite", "mysql", "no-scheme"],
)
def test_an_unusable_database_url_is_rejected_without_echoing_it(url):
    with pytest.raises(ConfigurationError) as excinfo:
        build_app({TOKEN_VARIABLE: TOKEN, DATABASE_URL_VARIABLE: url})
    message = str(excinfo.value)
    assert DATABASE_URL_VARIABLE in message
    assert DB_PASSWORD not in message


# --- a valid configuration -------------------------------------------------------


def test_build_app_needs_neither_docker_nor_a_reachable_database():
    app = build_app(ENV)  # the database URL points at a closed port
    assert isinstance(app, FastAPI)
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/servers").status_code == 401
    assert client.get("/docs").status_code == 404


def test_a_request_that_needs_the_database_answers_503_when_it_is_down():
    client = TestClient(build_app(ENV))
    response = client.get(
        "/servers", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "The platform database is unavailable."}
    assert DB_PASSWORD not in response.text


def test_the_database_connections_are_released_when_the_app_shuts_down(monkeypatch):
    class SpyServices:
        registry = object()
        closed = 0

        def close(self):
            SpyServices.closed += 1

    monkeypatch.setattr("app.api.entrypoint.build_services", lambda url: SpyServices())
    with TestClient(build_app(ENV)) as client:
        assert client.get("/health").status_code == 200
        assert SpyServices.closed == 0
    assert SpyServices.closed == 1


@pytest.mark.parametrize(
    "value, enabled",
    [("1", True), ("true", True), ("TRUE", True), ("0", False), ("false", False), ("", False), ("yes", False)],
)
def test_docs_flag_parsing(value, enabled):
    app = build_app({**ENV, DOCS_VARIABLE: value})
    assert (TestClient(app).get("/docs").status_code == 200) is enabled


# --- logging ------------------------------------------------------------------------


def test_configure_logging_is_idempotent_and_sets_the_level(clean_game_hosting_logger):
    logger = clean_game_hosting_logger
    configure_logging("debug")
    configure_logging("WARNING")
    ours = [h for h in logger.handlers if hasattr(h, "_game_hosting_handler")]
    assert len(ours) == 1
    assert logger.level == logging.WARNING


@pytest.mark.parametrize("level", ["verbose", "10", "critical!", "  "])
def test_invalid_log_level_fails_closed(clean_game_hosting_logger, level):
    with pytest.raises(ConfigurationError) as excinfo:
        configure_logging(level)
    assert LOG_LEVEL_VARIABLE in str(excinfo.value)


def test_build_app_rejects_an_invalid_log_level(clean_game_hosting_logger):
    with pytest.raises(ConfigurationError):
        build_app({**ENV, LOG_LEVEL_VARIABLE: "loud"})


def test_build_app_logs_the_configuration_without_any_secret(
    clean_game_hosting_logger, caplog
):
    with caplog.at_level(logging.INFO, logger="game_hosting"):
        build_app(ENV)
    messages = [record.getMessage() for record in caplog.records]
    assert any("docs disabled" in message for message in messages)
    assert any("gh_user:***@127.0.0.1:1/gh_db" in message for message in messages)
    assert not any(TOKEN in message or DB_PASSWORD in message for message in messages)


def test_enabling_docs_logs_a_warning(clean_game_hosting_logger, caplog):
    with caplog.at_level(logging.INFO, logger="game_hosting"):
        build_app({**ENV, DOCS_VARIABLE: "1"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "docs are enabled" in warnings[0].getMessage()


# --- real process -----------------------------------------------------------------------


def run_uvicorn(env_overrides, remove=()):
    env = {k: v for k, v in os.environ.items() if k not in remove}
    env.update(env_overrides)
    return subprocess.run(
        [
            sys.executable, "-m", "uvicorn", "--factory",
            "app.api.entrypoint:build_app", "--host", "127.0.0.1", "--port", "8799",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_uvicorn_process_refuses_to_start_without_a_token():
    result = run_uvicorn({DATABASE_URL_VARIABLE: DB_URL}, remove=(TOKEN_VARIABLE,))
    assert result.returncode == 1
    assert "ConfigurationError" in result.stderr
    assert TOKEN_VARIABLE in result.stderr


def test_uvicorn_process_refuses_to_start_without_a_database_url():
    result = run_uvicorn({TOKEN_VARIABLE: TOKEN}, remove=(DATABASE_URL_VARIABLE,))
    assert result.returncode == 1
    assert "ConfigurationError" in result.stderr
    assert DATABASE_URL_VARIABLE in result.stderr
