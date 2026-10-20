"""Fixtures for integration tests.

These tests talk to a real Docker Engine, but never touch the `minecraft-dev`
container or `./data`: each test works on a throwaway container with a unique
name that is force-removed afterwards. Run them with:

    python -m pytest -m integration
"""

import uuid

import docker
import pytest
from docker.errors import DockerException, ImageNotFound, NotFound
from requests.exceptions import RequestException

from app.infrastructure.docker_client import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    DockerClient,
)
from app.services.server_service import ServerService

TEST_IMAGE = "alpine:3"


@pytest.fixture(scope="session")
def sdk():
    try:
        client = docker.from_env()
        client.ping()
    except (DockerException, RequestException) as error:
        pytest.fail(f"Integration tests need a running Docker Engine: {error}")
    try:
        client.images.get(TEST_IMAGE)
    except ImageNotFound:
        client.images.pull(TEST_IMAGE)
    return client


def _throwaway_container(sdk, labels):
    name = f"game-hosting-it-{uuid.uuid4().hex[:8]}"
    sdk.containers.create(
        TEST_IMAGE,
        ["sh", "-c", "echo hello-from-it; exec sleep 300"],
        name=name,
        labels=labels,
    )
    return name


def _remove(sdk, name):
    try:
        sdk.containers.get(name).remove(force=True)
    except NotFound:
        pass


@pytest.fixture
def container_name(sdk):
    """Name of a created (not started) managed throwaway container that logs a
    line and then sleeps. `sleep` as PID 1 ignores SIGTERM, so stopping it also
    exercises the stop timeout path."""
    name = _throwaway_container(sdk, {MANAGED_LABEL: MANAGED_LABEL_VALUE})
    yield name
    _remove(sdk, name)


@pytest.fixture
def unmanaged_container_name(sdk):
    """Same throwaway container but WITHOUT the managed label."""
    name = _throwaway_container(sdk, {})
    yield name
    _remove(sdk, name)


@pytest.fixture
def service():
    return ServerService(DockerClient())
