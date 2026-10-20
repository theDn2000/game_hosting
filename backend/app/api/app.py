"""HTTP API.

The API is a thin layer over ServerService: routes validate input, call the
service and return its result. The service is injected by `create_app`, so the
caller (production entry point or tests) decides which runtime is behind it.

Application errors become HTTP errors with generic bodies; the underlying
detail (which may contain Docker messages or paths) is only logged.
"""

import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.errors import (
    GameHostingError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.services.server_service import (
    DEFAULT_LOG_TAIL,
    DEFAULT_STOP_TIMEOUT,
    ServerStatus,
)

logger = logging.getLogger("game_hosting.api")

# Valid Docker container names, limited to 64 characters.
SERVER_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
MAX_LOG_TAIL = 1000
MAX_STOP_TIMEOUT = 300

ServerName = Annotated[
    str, Path(pattern=SERVER_NAME_PATTERN, description="Server (container) name")
]
StopTimeout = Annotated[
    int,
    Query(ge=1, le=MAX_STOP_TIMEOUT, description="Seconds to wait before Docker kills the server"),
]
LogTail = Annotated[
    int, Query(ge=1, le=MAX_LOG_TAIL, description="Number of log lines to return")
]


class ServerStatusResponse(BaseModel):
    server: str
    status: ServerStatus


def get_service(request: Request):
    return request.app.state.service


Service = Annotated[object, Depends(get_service)]


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

    @app.exception_handler(ServerOperationError)
    async def handle_operation_failed(request, exc):
        logger.error("Server operation failed: %s", exc, exc_info=exc)
        return _error_response(500, "The operation failed.")

    @app.exception_handler(GameHostingError)
    async def handle_other_application_error(request, exc):
        logger.error("Unexpected application error: %s", exc, exc_info=exc)
        return _error_response(500, "The operation failed.")


def create_app(service):
    app = FastAPI(title="game_hosting API")
    app.state.service = service
    _register_error_handlers(app)

    @app.get("/health")
    def health():
        # Liveness only: deliberately does not call Docker.
        return {"status": "ok"}

    @app.get("/servers/{name}", response_model=ServerStatusResponse)
    def get_status(name: ServerName, service: Service):
        return ServerStatusResponse(server=name, status=service.get_server_status(name))

    @app.post("/servers/{name}/start", response_model=ServerStatusResponse)
    def start(name: ServerName, service: Service):
        return ServerStatusResponse(server=name, status=service.start_server(name))

    @app.post("/servers/{name}/stop", response_model=ServerStatusResponse)
    def stop(
        name: ServerName, service: Service, timeout: StopTimeout = DEFAULT_STOP_TIMEOUT
    ):
        return ServerStatusResponse(
            server=name, status=service.stop_server(name, timeout=timeout)
        )

    @app.post("/servers/{name}/restart", response_model=ServerStatusResponse)
    def restart(
        name: ServerName, service: Service, timeout: StopTimeout = DEFAULT_STOP_TIMEOUT
    ):
        return ServerStatusResponse(
            server=name, status=service.restart_server(name, timeout=timeout)
        )

    @app.get("/servers/{name}/logs", response_class=PlainTextResponse)
    def logs(name: ServerName, service: Service, tail: LogTail = DEFAULT_LOG_TAIL):
        return service.get_server_logs(name, tail=tail)

    return app
