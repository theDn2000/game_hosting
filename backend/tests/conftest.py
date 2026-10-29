"""Fixtures shared by the integration tests and the repository contract tests.

Everything here that needs Docker only runs when a test asks for it, and the
PostgreSQL used is always a throwaway container with a random password on a
random localhost port, removed with its volume afterwards. The platform
database from docker-compose.yml is never touched.
"""

import secrets
import time
import uuid

import docker
import pytest
from docker.errors import DockerException, ImageNotFound
from requests.exceptions import RequestException
from sqlalchemy import text

from app.errors import DatabaseUnavailableError
from app.infrastructure.db.database import check_connection, create_database_engine
from tests.support.migrations import upgrade
from tests.support.postgres import (
    POSTGRES_DB,
    POSTGRES_IMAGE,
    POSTGRES_STARTUP_TIMEOUT,
    POSTGRES_USER,
    build_postgres_url,
)


@pytest.fixture(scope="session")
def sdk():
    try:
        client = docker.from_env()
        client.ping()
    except (DockerException, RequestException) as error:
        pytest.fail(f"Integration tests need a running Docker Engine: {error}")
    return client


@pytest.fixture(scope="session")
def postgres_credentials(sdk):
    """A throwaway PostgreSQL container. Yields (password, host_port)."""
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
        engine = create_database_engine(build_postgres_url(password, port), connect_timeout=2)
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


@pytest.fixture(scope="session")
def postgres_url(postgres_credentials):
    password, port = postgres_credentials
    return build_postgres_url(password, port)


@pytest.fixture
def postgres_engine(postgres_url):
    engine = create_database_engine(postgres_url)
    yield engine
    engine.dispose()


class DatabaseFactory:
    """Creates empty databases inside the throwaway PostgreSQL server."""

    def __init__(self, credentials):
        self._password, self._port = credentials
        self._admin = create_database_engine(
            build_postgres_url(self._password, self._port), connect_timeout=5
        ).execution_options(isolation_level="AUTOCOMMIT")
        self._created = []

    def create(self):
        name = f"gh_{uuid.uuid4().hex[:12]}"
        with self._admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        self._created.append(name)
        return build_postgres_url(self._password, self._port, name)

    def close(self):
        with self._admin.connect() as connection:
            for name in self._created:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        self._admin.dispose()


@pytest.fixture(scope="session")
def database_factory(postgres_credentials):
    factory = DatabaseFactory(postgres_credentials)
    yield factory
    factory.close()


@pytest.fixture
def fresh_database_url(database_factory):
    """URL of a new, empty database (no tables)."""
    return database_factory.create()


@pytest.fixture(scope="session")
def migrated_database_url(database_factory):
    """URL of a database migrated to the latest revision, shared by many tests.
    Tests that use it must leave it clean (the contract tests truncate it)."""
    url = database_factory.create()
    engine = create_database_engine(url)
    try:
        upgrade(engine)
    finally:
        engine.dispose()
    return url
