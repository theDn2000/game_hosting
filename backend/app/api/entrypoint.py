"""Production-style entry point for the HTTP API.

`build_app` is the composition root of the API: it reads the configuration from
environment variables, configures logging, builds the real ServerService and
returns the app. It fails closed: without a valid token the app is not
created, so the API cannot start unprotected.

Run it from the backend/ directory, bound to localhost:

    python -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1

Environment variables:
    GAME_HOSTING_API_TOKEN  required, at least 16 characters (shared secret)
    GAME_HOSTING_API_DOCS   optional, "1" or "true" enables /docs (default: off)
    GAME_HOSTING_LOG_LEVEL  optional: DEBUG, INFO, WARNING or ERROR (default: INFO)
"""

import logging
import os

from app.api.app import MIN_TOKEN_LENGTH, create_app
from app.infrastructure.docker_client import DockerClient
from app.services.server_service import ServerService

TOKEN_VARIABLE = "GAME_HOSTING_API_TOKEN"
DOCS_VARIABLE = "GAME_HOSTING_API_DOCS"
LOG_LEVEL_VARIABLE = "GAME_HOSTING_LOG_LEVEL"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARKER = "_game_hosting_handler"

logger = logging.getLogger("game_hosting.api.entrypoint")


class ConfigurationError(Exception):
    """The API is not configured safely enough to start."""


def configure_logging(level_name="INFO"):
    """Send `game_hosting.*` logs to stderr. Safe to call more than once."""
    level_name = level_name.strip().upper()
    if level_name not in LOG_LEVELS:
        raise ConfigurationError(
            f"{LOG_LEVEL_VARIABLE} must be one of {', '.join(LOG_LEVELS)}"
        )
    root_logger = logging.getLogger("game_hosting")
    root_logger.setLevel(level_name)
    if not any(hasattr(handler, _HANDLER_MARKER) for handler in root_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        setattr(handler, _HANDLER_MARKER, True)
        root_logger.addHandler(handler)


def build_app(environ=None):
    environ = os.environ if environ is None else environ
    configure_logging(environ.get(LOG_LEVEL_VARIABLE) or "INFO")

    token = environ.get(TOKEN_VARIABLE, "")
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigurationError(
            f"{TOKEN_VARIABLE} must be set to a secret of at least "
            f"{MIN_TOKEN_LENGTH} characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    enable_docs = environ.get(DOCS_VARIABLE, "").strip().lower() in ("1", "true")
    service = ServerService(DockerClient())

    logger.info("API configured; interactive docs %s", "ENABLED" if enable_docs else "disabled")
    if enable_docs:
        logger.warning("Interactive docs are enabled; do not use this setting in production")
    return create_app(service, api_token=token, enable_docs=enable_docs)
