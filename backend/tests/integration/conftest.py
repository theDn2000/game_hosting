"""Fixtures for integration tests.

These tests talk to a real Docker Engine, but never touch the `minecraft-dev`
container or `./data`: each test works on a throwaway container with a unique
name that is force-removed afterwards. The Docker and PostgreSQL fixtures shared
with other tests are in tests/conftest.py. Run them with:

    python -m pytest -m integration
"""

import uuid

import pytest
from docker.errors import ImageNotFound, NotFound

from app.infrastructure.docker_client import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    DockerClient,
)
from app.services.server_service import ServerService

TEST_IMAGE = "alpine:3"


def _throwaway_container(sdk, labels):
    try:
        sdk.images.get(TEST_IMAGE)
    except ImageNotFound:
        sdk.images.pull(TEST_IMAGE)
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
