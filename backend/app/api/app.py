"""HTTP API.

The API is a thin layer over ServerRegistry: routes validate input, call the
registry and return its result. Servers are identified by their platform UUID;
container names are internal and never appear in responses. The registry is
injected by `create_app`, so the caller (production entry point or tests)
decides what is behind it.

Application errors become HTTP errors with generic bodies; the underlying
detail (which may contain Docker messages, paths or database details) is only
logged.

Every `/servers` route requires `Authorization: Bearer <token>`. This single
shared token is a temporary guard (for example against hostile web pages
reaching a localhost API), not user authentication: until users can
authenticate (Phase 4) it acts as the operator and may act on every server.
`/health` is open and never touches Docker or the database. The interactive
docs are disabled unless explicitly enabled.
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.domain.auth import OPERATOR
from app.domain.models import Game
from app.errors import (
    DatabaseUnavailableError,
    GameHostingError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.services.server_registry import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.server_service import (
    DEFAULT_LOG_TAIL,
    DEFAULT_STOP_TIMEOUT,
    ServerStatus,
)

logger = logging.getLogger("game_hosting.api")

MAX_LOG_TAIL = 1000
MAX_STOP_TIMEOUT = 300
MIN_TOKEN_LENGTH = 16

_bearer = HTTPBearer(auto_error=False)

PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Maximum servers to return")]
PageOffset = Annotated[int, Query(ge=0, description="Servers to skip")]
ServerId = Annotated[UUID, Path(description="Platform server id (UUID)")]
StopTimeout = Annotated[
    int,
    Query(ge=1, le=MAX_STOP_TIMEOUT, description="Seconds to wait before Docker kills the server"),
]
LogTail = Annotated[
    int, Query(ge=1, le=MAX_LOG_TAIL, description="Number of log lines to return")
]


class ServerResponse(BaseModel):
    """What the API shows about a server. Deliberately no container name and no
    owner details."""

    id: UUID
    name: str
    game: Game
    status: ServerStatus


def _to_response(registered):
    return ServerResponse(
        id=registered.server.id,
        name=registered.server.name,
        game=registered.server.game,
        status=registered.status,
    )


def get_registry(request: Request):
    return request.app.state.registry


Registry = Annotated[object, Depends(get_registry)]


def require_token(expected_token):
    expected = expected_token.encode("utf-8")

    def verify(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ):
        supplied = credentials.credentials.encode("utf-8") if credentials else b""
        if not secrets.compare_digest(supplied, expected):
            # %r keeps attacker-controlled path text from forging log lines.
            logger.warning(
                "Rejected request with a missing or invalid token: %s %r",
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify


def _error_response(status_code, detail):
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _register_error_handlers(app):
    @app.exception_handler(ServerNotFoundError)
    async def handle_not_found(request, exc):
        logger.info("Server not found: %s", exc.server_name)
        return _error_response(404, "Server not found.")

    @app.exception_handler(RuntimeUnavailableError)
    async def handle_runtime_unavailable(request, exc):
        # No traceback: the message already carries the cause, and a stopped
        # Docker would otherwise dump one on every request.
        logger.error("Server runtime unavailable: %s", exc)
        return _error_response(503, "The server runtime is unavailable.")

    @app.exception_handler(DatabaseUnavailableError)
    async def handle_database_unavailable(request, exc):
        # The message holds a redacted URL and the driver's text, never a password.
        logger.error("Database unavailable: %s", exc)
        return _error_response(503, "The platform database is unavailable.")

    @app.exception_handler(ServerOperationError)
    async def handle_operation_failed(request, exc):
        logger.error("Server operation failed: %s", exc, exc_info=exc)
        return _error_response(500, "The operation failed.")

    @app.exception_handler(GameHostingError)
    async def handle_other_application_error(request, exc):
        logger.error("Unexpected application error: %s", exc, exc_info=exc)
        return _error_response(500, "The operation failed.")


def create_app(registry, api_token, enable_docs=False, lifespan=None):
    if not api_token or len(api_token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"api_token must be at least {MIN_TOKEN_LENGTH} characters long"
        )
    docs_urls = {} if enable_docs else {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }
    app = FastAPI(title="game_hosting API", lifespan=lifespan, **docs_urls)
    app.state.registry = registry
    _register_error_handlers(app)
    protected = [Depends(require_token(api_token))]

    @app.get("/health")
    def health():
        # Liveness only: deliberately touches neither Docker nor the database.
        return {"status": "ok"}

    @app.get("/servers", response_model=list[ServerResponse], dependencies=protected)
    def list_servers(
        registry: Registry, limit: PageLimit = DEFAULT_PAGE_SIZE, offset: PageOffset = 0
    ):
        # Until sessions are wired in (Phase 4A.4) the shared token acts as the
        # local operator, who sees every server.
        page = registry.list_servers(OPERATOR, limit=limit, offset=offset)
        return [_to_response(r) for r in page.items]

    @app.get("/servers/{server_id}", response_model=ServerResponse, dependencies=protected)
    def get_server(server_id: ServerId, registry: Registry):
        return _to_response(registry.get_server(OPERATOR, server_id))

    @app.post("/servers/{server_id}/start", response_model=ServerResponse, dependencies=protected)
    def start(server_id: ServerId, registry: Registry):
        return _to_response(registry.start_server(OPERATOR, server_id))

    @app.post("/servers/{server_id}/stop", response_model=ServerResponse, dependencies=protected)
    def stop(
        server_id: ServerId, registry: Registry, timeout: StopTimeout = DEFAULT_STOP_TIMEOUT
    ):
        return _to_response(registry.stop_server(OPERATOR, server_id, timeout=timeout))

    @app.post("/servers/{server_id}/restart", response_model=ServerResponse, dependencies=protected)
    def restart(
        server_id: ServerId, registry: Registry, timeout: StopTimeout = DEFAULT_STOP_TIMEOUT
    ):
        return _to_response(registry.restart_server(OPERATOR, server_id, timeout=timeout))

    @app.get("/servers/{server_id}/logs", response_class=PlainTextResponse, dependencies=protected)
    def logs(server_id: ServerId, registry: Registry, tail: LogTail = DEFAULT_LOG_TAIL):
        return registry.get_server_logs(OPERATOR, server_id, tail=tail)

    return app
