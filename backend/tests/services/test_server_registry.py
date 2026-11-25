import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import GameServer, Plan, User
from app.errors import (
    AlreadyExistsError,
    InvalidDataError,
    PlanNotFoundError,
    ServerNotFoundError,
)
from app.services.server_registry import RegisteredServer, ServerRegistry
from app.services.server_service import ServerStatus
from tests.support.in_memory import InMemoryStore, InMemoryUnitOfWork

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


class FakeServerService:
    """Stand-in for ServerService, keyed by container name. Records calls and
    checks that no database unit of work is open while Docker is being used."""

    def __init__(self, containers=None, open_units=None):
        self.containers = dict(containers or {})
        self.calls = []
        self._open_units = open_units
        self.units_open_during_calls = []

    def _enter(self, *call):
        self.calls.append(call)
        if self._open_units is not None:
            self.units_open_during_calls.append(self._open_units["count"])

    def _require(self, name):
        if name not in self.containers:
            raise ServerNotFoundError(name)

    def get_server_status(self, name):
        self._enter("status", name)
        return self.containers.get(name, ServerStatus.NOT_FOUND)

    def start_server(self, name):
        self._enter("start", name)
        self._require(name)
        self.containers[name] = ServerStatus.RUNNING
        return ServerStatus.RUNNING

    def stop_server(self, name, timeout):
        self._enter("stop", name, timeout)
        self._require(name)
        self.containers[name] = ServerStatus.EXITED
        return ServerStatus.EXITED

    def restart_server(self, name, timeout):
        self._enter("restart", name, timeout)
        self._require(name)
        self.containers[name] = ServerStatus.RUNNING
        return ServerStatus.RUNNING

    def get_server_logs(self, name, tail):
        self._enter("logs", name, tail)
        self._require(name)
        return "log line\n"


class Harness:
    def __init__(self, containers=None):
        self.store = InMemoryStore()
        self.open_units = {"count": 0}
        self.service = FakeServerService(containers, self.open_units)

        def tracked_uow():
            return _TrackedUow(InMemoryUnitOfWork(self.store), self.open_units)

        self.registry = ServerRegistry(tracked_uow, self.service)

    def seed(self, email="ana@example.com", slug="dev", container="minecraft-dev", minutes=0):
        owner = User.new(email, created_at=T0)
        plan = Plan.new(slug, slug.title(), memory_mb=4096, cpu_millicores=2000)
        server = GameServer.new(
            owner.id, plan.id, "My world", container_name=container,
            created_at=T0 + timedelta(minutes=minutes),
        )
        with InMemoryUnitOfWork(self.store) as uow:
            if uow.users.find_by_email(owner.email) is None:
                uow.users.add(owner)
            else:
                owner = uow.users.find_by_email(owner.email)
            if uow.plans.find_by_slug(slug) is None:
                uow.plans.add(plan)
            else:
                plan = uow.plans.find_by_slug(slug)
            server = GameServer.new(
                owner.id, plan.id, "My world", container_name=container,
                created_at=T0 + timedelta(minutes=minutes),
            )
            uow.servers.add(server)
            uow.commit()
        return server, owner, plan


class _TrackedUow:
    def __init__(self, inner, counter):
        self._inner, self._counter = inner, counter

    def __enter__(self):
        self._counter["count"] += 1
        self._inner.__enter__()
        self.users, self.plans, self.servers = (
            self._inner.users, self._inner.plans, self._inner.servers,
        )
        self.sessions, self.login_failures = self._inner.sessions, self._inner.login_failures
        return self

    def __exit__(self, *exc):
        self._counter["count"] -= 1
        return self._inner.__exit__(*exc)

    def commit(self):
        self._inner.commit()

    def rollback(self):
        self._inner.rollback()


@pytest.fixture
def harness():
    return Harness({"minecraft-dev": ServerStatus.RUNNING})


# --- reading ------------------------------------------------------------------


def test_get_server_returns_the_server_owner_plan_and_live_status(harness):
    server, owner, plan = harness.seed()
    result = harness.registry.get_server(server.id)
    assert result == RegisteredServer(server, owner, plan, ServerStatus.RUNNING)
    assert harness.service.calls == [("status", "minecraft-dev")]


def test_get_unknown_server_raises_without_touching_docker(harness):
    with pytest.raises(ServerNotFoundError) as excinfo:
        harness.registry.get_server(uuid.uuid4())
    assert harness.service.calls == []
    assert "minecraft-dev" not in str(excinfo.value)


def test_a_registered_server_whose_container_is_gone_reports_not_found_status(harness):
    server, *_ = harness.seed(container="vanished")
    assert harness.registry.get_server(server.id).status is ServerStatus.NOT_FOUND


def test_list_servers_is_ordered_and_complete(harness):
    harness.service.containers.update({"b-container": ServerStatus.EXITED})
    late, *_ = harness.seed(container="minecraft-dev", minutes=30)
    early, owner, plan = harness.seed(container="b-container", minutes=1)
    result = harness.registry.list_servers()
    assert [r.server for r in result] == [early, late]
    assert [r.status for r in result] == [ServerStatus.EXITED, ServerStatus.RUNNING]
    assert result[0].owner == owner and result[0].plan == plan


def test_list_servers_when_empty(harness):
    assert harness.registry.list_servers() == []
    assert harness.service.calls == []


# --- lifecycle ----------------------------------------------------------------


def test_lifecycle_operations_translate_the_id_into_the_container_name(harness):
    server, *_ = harness.seed()
    assert harness.registry.stop_server(server.id).status is ServerStatus.EXITED
    assert harness.registry.start_server(server.id).status is ServerStatus.RUNNING
    assert harness.registry.restart_server(server.id, timeout=5).status is ServerStatus.RUNNING
    assert harness.registry.get_server_logs(server.id, tail=7) == "log line\n"
    assert harness.service.calls == [
        ("stop", "minecraft-dev", 30),
        ("start", "minecraft-dev"),
        ("restart", "minecraft-dev", 5),
        ("logs", "minecraft-dev", 7),
    ]


def test_default_log_tail_and_timeout_come_from_the_server_service(harness):
    server, *_ = harness.seed()
    harness.registry.get_server_logs(server.id)
    harness.registry.restart_server(server.id)
    assert ("logs", "minecraft-dev", 100) in harness.service.calls
    assert ("restart", "minecraft-dev", 30) in harness.service.calls


@pytest.mark.parametrize(
    "operation",
    [
        lambda r, i: r.start_server(i),
        lambda r, i: r.stop_server(i),
        lambda r, i: r.restart_server(i),
        lambda r, i: r.get_server_logs(i),
    ],
    ids=["start", "stop", "restart", "logs"],
)
def test_operations_on_an_unknown_id_never_reach_docker(harness, operation):
    harness.seed()
    with pytest.raises(ServerNotFoundError):
        operation(harness.registry, uuid.uuid4())
    assert harness.service.calls == []


def test_operating_on_a_server_whose_container_is_gone_is_not_found(harness):
    server, *_ = harness.seed(container="vanished")
    with pytest.raises(ServerNotFoundError):
        harness.registry.start_server(server.id)


def test_no_database_unit_is_open_while_docker_is_used(harness):
    server, *_ = harness.seed()
    harness.registry.start_server(server.id)
    harness.registry.get_server(server.id)
    harness.registry.list_servers()
    harness.registry.get_server_logs(server.id)
    assert harness.service.units_open_during_calls
    assert set(harness.service.units_open_during_calls) == {0}
    assert harness.open_units["count"] == 0


# --- adoption -----------------------------------------------------------------


def test_adopting_a_container_creates_the_owner_and_registers_the_server(harness):
    with InMemoryUnitOfWork(harness.store) as uow:
        plan = Plan.new("dev", "Dev", memory_mb=4096, cpu_millicores=2000)
        uow.plans.add(plan)
        uow.commit()
    adoption = harness.registry.adopt_container(
        "minecraft-dev", "Ana@Example.com", "dev", name="My world"
    )
    assert adoption.owner_created is True
    server = adoption.registered.server
    assert server.container_name == "minecraft-dev"
    assert server.name == "My world"
    assert adoption.registered.owner.email == "ana@example.com"
    assert adoption.registered.plan == plan
    assert adoption.registered.status is ServerStatus.RUNNING
    with InMemoryUnitOfWork(harness.store) as uow:  # persisted
        assert uow.servers.get(server.id) == server
        assert uow.users.find_by_email("ana@example.com") == adoption.registered.owner


def test_the_display_name_is_required_so_it_cannot_default_to_the_container_name(harness):
    harness.seed(email="seed@example.com")
    harness.service.containers["other"] = ServerStatus.EXITED
    with pytest.raises(TypeError):
        harness.registry.adopt_container("other", "ana@example.com", "dev")
    adoption = harness.registry.adopt_container("other", "ana@example.com", "dev", "Shown name")
    assert adoption.registered.server.name == "Shown name"
    assert adoption.registered.server.container_name == "other"


def test_adoption_reuses_an_existing_owner_ignoring_case(harness):
    harness.seed(email="ana@example.com")
    harness.service.containers["other"] = ServerStatus.EXITED
    adoption = harness.registry.adopt_container("other", "  ANA@example.COM ", "dev", "Second")
    assert adoption.owner_created is False
    with InMemoryUnitOfWork(harness.store) as uow:
        assert len(uow.servers.list_by_owner(adoption.registered.owner.id)) == 2
        assert len(harness.store.users) == 1


def test_adopting_the_same_container_twice_is_rejected(harness):
    harness.seed()
    with pytest.raises(AlreadyExistsError) as excinfo:
        harness.registry.adopt_container("minecraft-dev", "ana@example.com", "dev", "Again")
    assert excinfo.value.field == "container name"
    assert len(harness.store.servers) == 1


def test_adopting_with_an_unknown_plan_stores_nothing(harness):
    with pytest.raises(PlanNotFoundError):
        harness.registry.adopt_container("minecraft-dev", "new@example.com", "nope", "Name")
    assert not harness.store.users and not harness.store.servers  # no half-created owner


@pytest.mark.parametrize("container", ["missing", "unmanaged-or-gone"])
def test_adopting_a_container_that_is_not_managed_or_does_not_exist_stores_nothing(
    harness, container
):
    harness.seed(email="seed@example.com", container="existing")
    before = (dict(harness.store.users), dict(harness.store.servers))
    with pytest.raises(ServerNotFoundError):
        harness.registry.adopt_container(container, "new@example.com", "dev", "Name")
    assert (dict(harness.store.users), dict(harness.store.servers)) == before


def test_adopting_with_an_invalid_email_stores_nothing(harness):
    harness.seed(email="seed@example.com", container="existing")
    before = (dict(harness.store.users), dict(harness.store.servers))
    with pytest.raises(InvalidDataError):
        harness.registry.adopt_container("minecraft-dev", "not-an-email", "dev", "Name")
    assert (dict(harness.store.users), dict(harness.store.servers)) == before
