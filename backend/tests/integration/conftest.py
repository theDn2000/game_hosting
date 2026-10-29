"""Fixtures for integration tests.

These tests talk to a real Docker Engine, but never touch the `minecraft-dev`
container or `./data`: each test works on a throwaway container with a unique
name that is force-removed afterwards. Run them with:

    python -m pytest -m integration
"""

import secrets
import time
import uuid

import docker
import pytest
from docker.errors import DockerException, ImageNotFound, NotFound
from requests.exceptions import RequestException

from app.errors import DatabaseUnavailableError
from app.infrastructure.db.database import check_connection, create_database_engine
from app.infrastructure.docker_client import (
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    DockerClient,
)
from app.services.server_service import ServerService

TEST_IMAGE = "alpine:3"
# Same major version as docker-compose.yml.
POSTGRES_IMAGE = "postgres:18"
POSTGRES_USER = "gh_test"
POSTGRES_DB = "gh_test"
POSTGRES_STARTUP_TIMEOUT = 90


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


@pytest.fixture(scope="session")
def postgres_credentials(sdk):
    """A throwaway PostgreSQL container, published on a random localhost port.

    Yields (password, host_port). Never the platform database from
    docker-compose.yml: the container is new, unlabelled and removed with its
    anonymous volume afterwards.
    """
    try:
        sdk.images.get(POSTGRES_IMAGE)
    except ImageNotFound:
        sdk.images.pull(POSTGRES_IMAGE)
    password = secrets.token_urlsafe(24)
    name = f"game-hosting-it-pg-{uuid.uuid4().hex[:8]}"
    container = sdk.containers.run(
        POSTGRES_IMAGE,
        detach=True,
        name=name,
        environment={
            "POSTGRES_USER": POSTGRES_USER,
            "POSTGRES_DB": POSTGRES_DB,
            "POSTGRES_PASSWORD": password,
        },
        ports={"5432/tcp": ("127.0.0.1", None)},
    )
    try:
        container.reload()
        port = int(container.ports["5432/tcp"][0]["HostPort"])
        engine = create_database_engine(
            _postgres_url(password, port), connect_timeout=2
        )
        deadline = time.monotonic() + POSTGRES_STARTUP_TIMEOUT
        while True:
            try:
                check_connection(engine)
                break
            except DatabaseUnavailableError:
                if time.monotonic() > deadline:
                    logs = container.logs(tail=20).decode(errors="replace")
                    pytest.fail(f"PostgreSQL did not start in time:\n{logs}")
                time.sleep(1)
        engine.dispose()
        yield password, port
    finally:
        container.remove(force=True, v=True)


def _postgres_url(password, port):
    return (
        f"postgresql+psycopg://{POSTGRES_USER}:{password}"
        f"@127.0.0.1:{port}/{POSTGRES_DB}"
    )


@pytest.fixture(scope="session")
def postgres_url(postgres_credentials):
    password, port = postgres_credentials
    return _postgres_url(password, port)


@pytest.fixture
def postgres_engine(postgres_url):
    engine = create_database_engine(postgres_url)
    yield engine
    engine.dispose()
