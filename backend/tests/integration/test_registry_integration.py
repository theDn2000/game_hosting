"""ServerRegistry on real PostgreSQL and real Docker (no HTTP, no subprocess)."""

import pytest
from sqlalchemy import text

from app.composition import build_services
from app.errors import AlreadyExistsError, PlanNotFoundError, ServerNotFoundError
from app.infrastructure.db.database import create_database_engine
from app.infrastructure.docker_client import MANAGED_LABEL, MANAGED_LABEL_VALUE
from app.services.server_service import ServerStatus
from tests.integration.conftest import _remove, _throwaway_container

pytestmark = pytest.mark.integration


@pytest.fixture
def services(migrated_database_url):
    engine = create_database_engine(migrated_database_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE sessions, login_failures, game_servers, plans, users"))
    engine.dispose()
    services = build_services(migrated_database_url)
    services.plans.add_plan("dev", "Dev", memory_mb=512, cpu_millicores=500)
    yield services
    services.close()


def row_counts(services):
    with services.engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("users", "plans", "game_servers")
        }


def test_adopting_a_real_container_and_driving_it_through_the_registry(services, container_name):
    adoption = services.registry.adopt_container(
        container_name, "Ana@Example.com", "dev", "Registry world"
    )
    server_id = adoption.registered.server.id
    assert adoption.owner_created is True
    assert adoption.registered.status is ServerStatus.CREATED
    assert row_counts(services) == {"users": 1, "plans": 1, "game_servers": 1}

    assert services.registry.start_server(server_id).status is ServerStatus.RUNNING
    assert "hello-from-it" in services.registry.get_server_logs(server_id, tail=5)
    assert services.registry.restart_server(server_id, timeout=1).status is ServerStatus.RUNNING
    assert services.registry.stop_server(server_id, timeout=1).status is ServerStatus.EXITED
    fetched = services.registry.get_server(server_id)
    assert fetched.server.container_name == container_name
    assert fetched.owner.email == "ana@example.com"
    assert [r.server.id for r in services.registry.list_servers()] == [server_id]


def test_a_second_container_for_the_same_owner_reuses_the_user(services, container_name, sdk):
    services.registry.adopt_container(container_name, "ana@example.com", "dev", "First")
    # Second managed container, created by hand just for this test.
    other = _throwaway_container(sdk, {MANAGED_LABEL: MANAGED_LABEL_VALUE})
    try:
        adoption = services.registry.adopt_container(other, "ANA@example.com", "dev", "Second")
    finally:
        _remove(sdk, other)
    assert adoption.owner_created is False
    assert row_counts(services) == {"users": 1, "plans": 1, "game_servers": 2}


def test_a_failed_adoption_leaves_nothing_behind(services, container_name):
    with pytest.raises(PlanNotFoundError):
        services.registry.adopt_container(container_name, "new@example.com", "no-such-plan", "N")
    assert row_counts(services) == {"users": 0, "plans": 1, "game_servers": 0}  # no half-created owner


def test_adopting_the_same_container_twice_is_rejected_by_the_database(services, container_name):
    services.registry.adopt_container(container_name, "ana@example.com", "dev", "Once")
    with pytest.raises(AlreadyExistsError) as excinfo:
        services.registry.adopt_container(container_name, "bob@example.com", "dev", "Twice")
    assert excinfo.value.field == "container name"
    assert row_counts(services) == {"users": 1, "plans": 1, "game_servers": 1}  # bob was not created


def test_an_unmanaged_container_cannot_be_adopted(services, unmanaged_container_name):
    with pytest.raises(ServerNotFoundError):
        services.registry.adopt_container(
            unmanaged_container_name, "ana@example.com", "dev", "Nope"
        )
    assert row_counts(services) == {"users": 0, "plans": 1, "game_servers": 0}
