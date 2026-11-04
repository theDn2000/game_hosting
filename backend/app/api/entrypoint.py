"""Production-style entry point for the HTTP API.

`build_app` is the composition root of the API: it reads the configuration from
environment variables, configures logging, builds the real services and
returns the app. It fails closed: without a valid token and a usable database
URL the app is not created, so the API cannot start unprotected or without its
database.

Run it from the backend/ directory, bound to localhost:

    python -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1

Environment variables:
    GAME_HOSTING_API_TOKEN    required, at least 16 characters (shared secret)
    GAME_HOSTING_DATABASE_URL required, postgresql+psycopg://user:password@host:port/db
    GAME_HOSTING_API_DOCS     optional, "1" or "true" enables /docs (default: off)
    GAME_HOSTING_LOG_LEVEL    optional: DEBUG, INFO, WARNING or ERROR (default: INFO)

The database is only contacted when a request needs it, so the API starts even
if PostgreSQL is down (requests then answer 503). Apply the migrations first:
`python -m alembic upgrade head`.
"""

import logging
import os
from contextlib import asynccontextmanager

from app.api.app import MIN_TOKEN_LENGTH, create_app
from app.composition import build_services
from app.errors import ConfigurationError
from app.infrastructure.db.database import DATABASE_URL_VARIABLE, redact_url

TOKEN_VARIABLE = "GAME_HOSTING_API_TOKEN"
DOCS_VARIABLE = "GAME_HOSTING_API_DOCS"
LOG_LEVEL_VARIABLE = "GAME_HOSTING_LOG_LEVEL"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARKER = "_game_hosting_handler"

logger = logging.getLogger("game_hosting.api.entrypoint")


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
    database_url = environ.get(DATABASE_URL_VARIABLE, "")
    if not database_url:
        raise ConfigurationError(
            f"{DATABASE_URL_VARIABLE} must be set to the database URL "
            "(postgresql+psycopg://user:password@host:port/dbname)."
        )
    try:
        services = build_services(database_url)
    except ValueError as error:
        # The message never contains the URL, so it is safe to show.
        raise ConfigurationError(f"{DATABASE_URL_VARIABLE} is not usable: {error}") from None
    enable_docs = environ.get(DOCS_VARIABLE, "").strip().lower() in ("1", "true")

    @asynccontextmanager
    async def lifespan(app):
        yield
        services.close()

    logger.info("API configured; database %s", redact_url(database_url))
    logger.info("Interactive docs %s", "ENABLED" if enable_docs else "disabled")
    if enable_docs:
        logger.warning("Interactive docs are enabled; do not use this setting in production")
    return create_app(
        services.registry, api_token=token, enable_docs=enable_docs, lifespan=lifespan
    )
