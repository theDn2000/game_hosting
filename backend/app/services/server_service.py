from enum import StrEnum

from app.errors import ServerNotFoundError
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

    def __init__(self, docker=None):
        self.docker = docker if docker is not None else DockerClient()

    def get_server_status(self, server_name):
        """Return the server status; a server that does not exist is a valid
        answer (`ServerStatus.NOT_FOUND`), not an error."""
        try:
            return self._current_status(server_name)
        except ServerNotFoundError:
            return ServerStatus.NOT_FOUND

    def start_server(self, server_name):
        if self._current_status(server_name) != ServerStatus.RUNNING:
            self.docker.start_container(server_name)
        return self._current_status(server_name)

    def stop_server(self, server_name, timeout=DEFAULT_STOP_TIMEOUT):
        if self._current_status(server_name) not in _NOT_RUNNING:
            self.docker.stop_container(server_name, timeout=timeout)
        return self._current_status(server_name)

    def restart_server(self, server_name, timeout=DEFAULT_STOP_TIMEOUT):
        self.docker.restart_container(server_name, timeout=timeout)
        return self._current_status(server_name)

    def get_server_logs(self, server_name, tail=DEFAULT_LOG_TAIL):
        if tail < 1:
            raise ValueError("tail must be a positive number of lines")
        return self.docker.get_logs(server_name, tail=tail)

    def _current_status(self, server_name):
        """Status of an existing server; raises ServerNotFoundError otherwise."""
        raw_status = self.docker.get_status(server_name)
        try:
            return ServerStatus(raw_status)
        except ValueError:
            return ServerStatus.UNKNOWN
