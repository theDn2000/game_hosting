from contextlib import contextmanager

import docker
from docker.errors import APIError, DockerException, NotFound
from requests.exceptions import RequestException

from app.errors import (
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)


class DockerClient:
    """Thin adapter over the Docker SDK.

    The connection to Docker is created on first use, so building this object
    never fails when Docker is not running. Docker SDK exceptions are
    translated into application errors and never leave this class.
    """

    def __init__(self, client=None):
        self._client = client

    def get_status(self, container_name):
        """Return Docker's status string for the container (e.g. "running")."""
        return self._get_container(container_name).status

    def start_container(self, container_name):
        container = self._get_container(container_name)
        with self._translate_errors(container_name, "start"):
            container.start()

    def stop_container(self, container_name, timeout):
        container = self._get_container(container_name)
        with self._translate_errors(container_name, "stop"):
            container.stop(timeout=timeout)

    def restart_container(self, container_name, timeout):
        container = self._get_container(container_name)
        with self._translate_errors(container_name, "restart"):
            container.restart(timeout=timeout)

    def get_logs(self, container_name, tail):
        container = self._get_container(container_name)
        with self._translate_errors(container_name, "read logs of"):
            raw_logs = container.logs(tail=tail)
        return raw_logs.decode("utf-8", errors="replace")

    def _get_client(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _get_container(self, container_name):
        with self._translate_errors(container_name, "look up"):
            try:
                return self._get_client().containers.get(container_name)
            except NotFound as exc:
                raise ServerNotFoundError(container_name) from exc

    @contextmanager
    def _translate_errors(self, container_name, action):
        try:
            yield
        except APIError as exc:
            raise ServerOperationError(
                f"Docker failed to {action} '{container_name}': {exc}"
            ) from exc
        except (DockerException, RequestException) as exc:
            raise RuntimeUnavailableError(
                f"Cannot reach Docker while trying to {action} "
                f"'{container_name}': {exc}"
            ) from exc
