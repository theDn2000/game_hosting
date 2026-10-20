"""Production-style entry point for the HTTP API.

`build_app` is the composition root of the API: it reads the configuration from
environment variables, builds the real ServerService and returns the app. It
fails closed: without a valid token the app is not created, so the API cannot
start unprotected.

Run it from the backend/ directory, bound to localhost:

    python -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1

Environment variables:
    GAME_HOSTING_API_TOKEN  required, at least 16 characters (shared secret)
    GAME_HOSTING_API_DOCS   optional, "1" or "true" enables /docs (default: off)
"""

import os

from app.api.app import MIN_TOKEN_LENGTH, create_app
from app.infrastructure.docker_client import DockerClient
from app.services.server_service import ServerService

TOKEN_VARIABLE = "GAME_HOSTING_API_TOKEN"
DOCS_VARIABLE = "GAME_HOSTING_API_DOCS"


class ConfigurationError(Exception):
    """The API is not configured safely enough to start."""


def build_app(environ=None):
    environ = os.environ if environ is None else environ
    token = environ.get(TOKEN_VARIABLE, "")
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigurationError(
            f"{TOKEN_VARIABLE} must be set to a secret of at least "
            f"{MIN_TOKEN_LENGTH} characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    enable_docs = environ.get(DOCS_VARIABLE, "").strip().lower() in ("1", "true")
    service = ServerService(DockerClient())
    return create_app(service, api_token=token, enable_docs=enable_docs)
