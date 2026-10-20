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

TOKEN = "env-token-0123456789abcdef"
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


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


@pytest.fixture
def clean_game_hosting_logger():
    logger = logging.getLogger("game_hosting")
    saved_handlers, saved_level = list(logger.handlers), logger.level
    yield logger
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


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
        build_app({TOKEN_VARIABLE: TOKEN, LOG_LEVEL_VARIABLE: "loud"})


def test_build_app_logs_the_configuration_without_the_token(
    clean_game_hosting_logger, caplog
):
    with caplog.at_level(logging.INFO, logger="game_hosting"):
        build_app({TOKEN_VARIABLE: TOKEN})
    messages = [record.getMessage() for record in caplog.records]
    assert any("docs disabled" in message for message in messages)
    assert not any(TOKEN in message for message in messages)


def test_enabling_docs_logs_a_warning(clean_game_hosting_logger, caplog):
    with caplog.at_level(logging.INFO, logger="game_hosting"):
        build_app({TOKEN_VARIABLE: TOKEN, DOCS_VARIABLE: "1"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "docs are enabled" in warnings[0].getMessage()


def test_uvicorn_process_refuses_to_start_without_a_token():
    env = {k: v for k, v in os.environ.items() if k != TOKEN_VARIABLE}
    result = subprocess.run(
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
    assert result.returncode == 1
    assert "ConfigurationError" in result.stderr
    assert TOKEN_VARIABLE in result.stderr
