from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from app.errors import ServerNotFoundError

if TYPE_CHECKING:
    from app.infrastructure.docker_client import DockerClient

DEFAULT_STOP_TIMEOUT = 30
DEFAULT_LOG_TAIL = 100


class ServerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESTARTING = "restarting"
    REMOVING = "removing"
    EXITED = "exited"
    DEAD = "dead"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


_NOT_RUNNING = {ServerStatus.CREATED, ServerStatus.EXITED, ServerStatus.DEAD}


class ServerService:
    """Application-level operations on a game server.

    Lifecycle operations are idempotent: starting a running server or stopping
    a stopped one is not an error. Each returns the final status. Errors are
    the application errors defined in `app.errors`.
    """

    def __init__(self, docker_client: DockerClient):
        # Injected by the entry point (CLI, API) so this layer never imports a
        # concrete infrastructure adapter at runtime.
        self.docker_client = docker_client

    def get_server_status(self, server_name):
        """Return the server status; a server that does not exist is a valid
        answer (`ServerStatus.NOT_FOUND`), not an error."""
        try:
            return self._current_status(server_name)
        except ServerNotFoundError:
            return ServerStatus.NOT_FOUND

    def get_server_statuses(self, server_names):
        """Statuses of many servers with one Docker call: {name: status}, with
        `ServerStatus.NOT_FOUND` for the ones that do not exist."""
        names = list(server_names)
        raw = self.docker_client.get_statuses(names)
        return {
            name: self._to_status(raw[name]) if name in raw else ServerStatus.NOT_FOUND
            for name in names
        }

    def start_server(self, server_name):
        if self._current_status(server_name) != ServerStatus.RUNNING:
            self.docker_client.start_container(server_name)
        return self._current_status(server_name)

    def stop_server(self, server_name, timeout=DEFAULT_STOP_TIMEOUT):
        if self._current_status(server_name) not in _NOT_RUNNING:
            self.docker_client.stop_container(server_name, timeout=timeout)
        return self._current_status(server_name)

    def restart_server(self, server_name, timeout=DEFAULT_STOP_TIMEOUT):
        self.docker_client.restart_container(server_name, timeout=timeout)
        return self._current_status(server_name)

    def get_server_logs(self, server_name, tail=DEFAULT_LOG_TAIL):
        if tail < 1:
            raise ValueError("tail must be a positive number of lines")
        return self.docker_client.get_logs(server_name, tail=tail)

    def _current_status(self, server_name):
        """Status of an existing server; raises ServerNotFoundError otherwise."""
        return self._to_status(self.docker_client.get_status(server_name))

    @staticmethod
    def _to_status(raw_status):
        try:
            return ServerStatus(raw_status)
        except ValueError:
            return ServerStatus.UNKNOWN
